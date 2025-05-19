# coding: utf-8
from flask import Flask, request, jsonify, render_template, send_from_directory, make_response
import os
import shutil
import time
import requests
import rarfile # For .rar files
import zipfile # For .zip files
import tarfile # For .tar, .tar.gz, .tar.bz2 files
import py7zr    # For .7z files
import re
import subprocess
import json
import hashlib
import logging # For better logging
from urllib.parse import urlparse # Added for URL parsing
from celery import Celery
import mimetypes

# Import database models and utilities
from .models import init_db, ArchiveMetadata
from .db_utils import migrate_json_cache_to_db, get_metadata_by_url_hash, create_or_update_metadata, update_metadata_status

# --- إعداد التسجيل ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Celery Configuration ---
# Default fallback to memory broker if Redis isn't available
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')

# Check Redis connectivity and fall back if needed
def check_redis_connectivity():
    import socket
    from urllib.parse import urlparse
    
    try:
        # Parse Redis URL
        redis_url = urlparse(CELERY_BROKER_URL)
        host = redis_url.hostname or 'localhost'
        port = redis_url.port or 6379
        
        # Try to connect
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((host, port))
        s.close()
        logger.info(f"Redis connection successful at {host}:{port}")
        return True
    except Exception as e:
        logger.warning(f"Redis connectivity check failed: {e}")
        return False

# Configure Celery with fallback options if Redis is not available
redis_available = check_redis_connectivity()
if not redis_available:
    logger.warning("Redis not available. Falling back to eager execution mode for development.")
    CELERY_BROKER_URL = 'memory://'
    CELERY_RESULT_BACKEND = 'cache'
    CELERY_CACHE_BACKEND = 'memory'

celery_app = Celery(__name__, broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

# If Redis isn't available, use eager mode for development
if not redis_available:
    celery_app.conf.update(
        task_always_eager=True,  # Tasks execute locally instead of being sent to queue
        task_eager_propagates=True,  # Propagate exceptions in eager mode
    )

# --- الثوابت والمسارات ---
_current_file_dir = os.path.dirname(os.path.abspath(__file__))
BASE_APP_DIR = _current_file_dir

TEMPLATE_DIR = os.path.join(BASE_APP_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_APP_DIR, 'static')

UPLOAD_DIR_FLASK_APP = os.path.join(BASE_APP_DIR, 'uploads_flask')
TEMP_ARCHIVE_DIR_FLASK_APP = os.path.join(UPLOAD_DIR_FLASK_APP, 'temp_archive')
EXTRACTED_FILES_DIR_FLASK_APP = os.path.join(UPLOAD_DIR_FLASK_APP, 'extracted_files')

MEGADL_EXEC_PATH = "megadl"
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg')

REQUESTS_CONNECT_TIMEOUT = 30  # seconds
REQUESTS_READ_TIMEOUT = 1800   # seconds (30 minutes)
MEGADL_TIMEOUT = 3600          # seconds (1 hour)
DOWNLOAD_CHUNK_SIZE = 8192 * 4 # 32KB chunk size for downloads

for dir_path in [TEMPLATE_DIR, STATIC_DIR, UPLOAD_DIR_FLASK_APP, TEMP_ARCHIVE_DIR_FLASK_APP, EXTRACTED_FILES_DIR_FLASK_APP]:
    os.makedirs(dir_path, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# Initialize the database
db_session = init_db(BASE_APP_DIR)

# Configuration for internal redirect offloading (Nginx X-Accel-Redirect or Apache X-Sendfile)
USE_X_ACCEL_REDIRECT = os.environ.get('USE_X_ACCEL_REDIRECT', 'False').lower() in ('true','1')
INTERNAL_REDIRECT_URL_PREFIX = os.environ.get('INTERNAL_REDIRECT_URL_PREFIX', '/internal_protected_files')

# Legacy JSON cache file path (for migration)
CACHE_FILENAME = os.path.join(UPLOAD_DIR_FLASK_APP, '.url_cache.json')

# Migrate existing JSON cache data to database on startup
migrate_json_cache_to_db(CACHE_FILENAME, db_session)

# --- Database for Metadata Implementation ---
# We've replaced the JSON cache-based approach with SQLAlchemy database storage
# Benefits:
# - Better performance for large numbers of archives
# - Proper indexing for faster lookups
# - Transactional integrity
# - Support for complex queries (e.g., finding old/unused archives)
# - Additional metadata storage (original URL, timestamps, status)
# - Better concurrency support for multiple workers

def get_google_drive_direct_link(sharing_url):
    file_id = None
    match = re.search(r'/file/d/([^/]+)', sharing_url)
    if match:
        file_id = match.group(1)
    if file_id:
        return f'https://drive.google.com/uc?export=download&id={file_id}'
    else:
        logger.warning(f"لم يمكن استخلاص معرّف الملف من رابط جوجل درايف: {sharing_url}.")
        return sharing_url

def cleanup_old_session_data(session_id):
    logger.info(f"محاولة تنظيف بيانات الجلسة المؤقتة للمعرّف: {session_id}")
    temp_session_folder = os.path.join(TEMP_ARCHIVE_DIR_FLASK_APP, session_id)
    try:
        if os.path.exists(temp_session_folder):
            shutil.rmtree(temp_session_folder)
            logger.info(f"تم حذف مجلد الأرشيف المؤقت: {temp_session_folder}")
    except Exception as e:
        logger.error(f"خطأ أثناء حذف المجلد المؤقت {temp_session_folder}: {e}")

def build_file_structure(start_path, session_id):
    structure = {'name': 'root', 'type': 'directory', 'path': '', 'children': []}
    structure_file_path = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id, '.archive_structure.json')

    for root, dirs, files in os.walk(start_path):
        rel_root = os.path.relpath(root, start_path)
        if rel_root == '.': rel_root = ''

        parent_node = structure
        if rel_root:
            path_parts = rel_root.split(os.sep)
            current_search_node = structure
            found_parent = True
            for part in path_parts:
                child_node = next((c for c in current_search_node.get('children', []) if c['name'] == part and c['type'] == 'directory'), None)
                if child_node:
                    current_search_node = child_node
                else:
                    logger.error(f"لم يتم العثور على العقدة الأصل لـ {rel_root} (جزء: {part}). تخطي العناصر في هذا المسار.")
                    found_parent = False
                    break
            if found_parent:
                parent_node = current_search_node
            else:
                continue

        dirs.sort()
        files.sort()

        for d_name in dirs:
            if d_name == '.archive_structure.json': continue
            dir_path_abs = os.path.join(root, d_name)
            dir_path_rel = os.path.relpath(dir_path_abs, start_path).replace(os.sep, '/')
            dir_node = {'name': d_name, 'type': 'directory', 'path': dir_path_rel, 'children': []}
            parent_node['children'].append(dir_node)

        for f_name in files:
            if f_name == '.archive_structure.json': continue
            file_path_abs = os.path.join(root, f_name)
            file_path_rel = os.path.relpath(file_path_abs, start_path).replace(os.sep, '/')
            is_image = f_name.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
            file_node = {'name': f_name, 'type': 'file', 'path': file_path_rel, 'is_image': is_image}
            parent_node['children'].append(file_node)
    try:
        os.makedirs(os.path.dirname(structure_file_path), exist_ok=True)
        with open(structure_file_path, 'w', encoding='utf-8') as f:
            json.dump(structure, f, ensure_ascii=False, indent=2)
        logger.info(f"تم حفظ الهيكل الجديد في ملف الكاش: {structure_file_path}")
    except Exception as e:
        logger.error(f"خطأ في حفظ ملف الهيكل {structure_file_path}: {e}")

    return structure

def get_archive_type(filepath):
    if not os.path.exists(filepath):
        logger.warning(f"File not found for type checking: {filepath}")
        return None

    filename = os.path.basename(filepath).lower()

    try:
        if rarfile.is_rarfile(filepath):
            logger.debug(f"Identified as RAR by rarfile: {filepath}")
            return 'rar'
    except Exception as e:
        logger.debug(f"Not a RAR file or error checking RAR for {filepath}: {e}")
        pass

    try:
        if zipfile.is_zipfile(filepath):
            logger.debug(f"Identified as ZIP by zipfile: {filepath}")
            return 'zip'
    except Exception as e:
        logger.debug(f"Not a ZIP file or error checking ZIP for {filepath}: {e}")
        pass

    try:
        if tarfile.is_tarfile(filepath):
            logger.debug(f"Identified as TAR by tarfile: {filepath}")
            return 'tar'
    except Exception as e:
        logger.debug(f"Not a TAR file or error checking TAR for {filepath}: {e}")
        pass

    if filename.endswith('.7z'):
        try:
            with py7zr.SevenZipFile(filepath, 'r') as _:
                logger.debug(f"Identified as 7Z by py7zr opening: {filepath}")
                return '7z'
        except py7zr.exceptions.Bad7zFile:
            logger.debug(f"File {filepath} has .7z extension but is not a valid 7z archive.")
        except Exception as e:
            logger.warning(f"Error checking 7z file {filepath} with py7zr: {e}")

    logger.debug(f"Falling back to extension-based type check for {filepath}")
    if filename.endswith('.rar'):
        logger.debug(f"Identified as RAR by extension: {filepath}")
        return 'rar'
    if filename.endswith('.zip'):
        logger.debug(f"Identified as ZIP by extension: {filepath}")
        return 'zip'
    if filename.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
        logger.debug(f"Identified as TAR by extension: {filepath}")
        return 'tar'
    if filename.endswith('.7z'):
        logger.debug(f"Identified as 7Z by extension (after py7zr check if applicable): {filepath}")
        return '7z'

    logger.info(f"Could not determine archive type for {filepath} using libraries or common extensions.")
    return None

@celery_app.task(bind=True, name='app.process_archive_task')
def process_archive_task(self, original_archive_url, session_id, url_hash):
    task_id = self.request.id
    logger.info(f"Celery Task {task_id} started for Session ID: {session_id}, URL: {original_archive_url}")

    # Update task ID in database
    create_or_update_metadata(db_session, url_hash, original_archive_url, session_id, task_id=task_id, status='STARTED')

    temp_session_folder = os.path.join(TEMP_ARCHIVE_DIR_FLASK_APP, session_id)
    extracted_session_folder = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id)

    local_archive_filename = "archive_download"
    local_archive_path = os.path.join(temp_session_folder, local_archive_filename)
    download_successful = False
    archive_type = None

    try:
        logger.info(f"Task {task_id}: بدء التحميل للجلسة {session_id} من {original_archive_url}")
        if "mega.nz" in original_archive_url or "mega.co.nz" in original_archive_url:
            logger.info(f"Task {task_id}: رابط MEGA. استخدام MEGADL_EXEC_PATH: {MEGADL_EXEC_PATH}")
            if not MEGADL_EXEC_PATH or (MEGADL_EXEC_PATH == "megadl" and not shutil.which("megadl")) and not os.path.exists(MEGADL_EXEC_PATH):
                 raise FileNotFoundError(f"أداة megadl غير موجودة أو غير قابلة للتنفيذ: {MEGADL_EXEC_PATH}")

            megadl_command = [MEGADL_EXEC_PATH, original_archive_url, "--path", temp_session_folder]
            logger.info(f"Task {task_id}: Executing megadl command: {' '.join(megadl_command)} with timeout {MEGADL_TIMEOUT}s")
            process = subprocess.run(megadl_command, capture_output=True, text=True, check=False, timeout=MEGADL_TIMEOUT)

            if process.stdout: logger.info(f"Task {task_id}: megadl stdout: {process.stdout.strip()}")
            if process.stderr: logger.error(f"Task {task_id}: megadl stderr: {process.stderr.strip()}")

            if process.returncode == 0:
                downloaded_files_mega = [f for f in os.listdir(temp_session_folder) if os.path.isfile(os.path.join(temp_session_folder, f))]
                if downloaded_files_mega:
                    actual_downloaded_filename = downloaded_files_mega[0]
                    local_archive_path = os.path.join(temp_session_folder, actual_downloaded_filename)
                    download_successful = True
                    logger.info(f"Task {task_id}: تم تحميل MEGA بنجاح: {local_archive_path}")
                else:
                    raise Exception("megadl انتهى بنجاح (رمز 0) ولكن لم يتم العثور على ملف في المجلد المؤقت.")
            else:
                error_message = process.stderr.strip() or process.stdout.strip() or "Unknown megadl error"
                raise Exception(f"فشل megadl. الرمز: {process.returncode}. الخطأ: {error_message}")

        elif "drive.google.com" in original_archive_url:
            download_url = get_google_drive_direct_link(original_archive_url)
            logger.info(f"Task {task_id}: رابط Google Drive. الرابط المباشر: {download_url}. Timeout: C={REQUESTS_CONNECT_TIMEOUT}s, R={REQUESTS_READ_TIMEOUT}s")
            headers = {'User-Agent': 'Mozilla/5.0'}
            with requests.get(download_url, headers=headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT), allow_redirects=True) as r:
                r.raise_for_status()
                current_filename_for_download = local_archive_filename
                content_disposition = r.headers.get('content-disposition')
                if content_disposition:
                    fname_match = re.findall('filename="?([^"]+)"?', content_disposition)
                    if fname_match:
                        current_filename_for_download = fname_match[0]

                local_archive_path = os.path.join(temp_session_folder, current_filename_for_download)
                with open(local_archive_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)
            download_successful = True
            logger.info(f"Task {task_id}: تم تحميل Google Drive بنجاح: {local_archive_path}")
        else:
            download_url = original_archive_url
            logger.info(f"Task {task_id}: تحميل رابط مباشر: {download_url}. Timeout: C={REQUESTS_CONNECT_TIMEOUT}s, R={REQUESTS_READ_TIMEOUT}s")
            headers = {'User-Agent': 'Mozilla/5.0'}
            with requests.get(download_url, headers=headers, stream=True, timeout=(REQUESTS_CONNECT_TIMEOUT, REQUESTS_READ_TIMEOUT), allow_redirects=True) as r:
                r.raise_for_status()
                current_filename_for_download = local_archive_filename
                content_disposition = r.headers.get('content-disposition')
                if content_disposition:
                    fname_match = re.findall('filename="?([^"]+)"?', content_disposition)
                    if fname_match:
                        current_filename_for_download = fname_match[0]
                else:
                    parsed_url_path = urlparse(download_url).path
                    if parsed_url_path:
                        url_fn = os.path.basename(parsed_url_path)
                        if url_fn:
                             current_filename_for_download = url_fn

                local_archive_path = os.path.join(temp_session_folder, current_filename_for_download)
                with open(local_archive_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)
            download_successful = True
            logger.info(f"Task {task_id}: تم التحميل المباشر بنجاح: {local_archive_path}")

        if not download_successful or not os.path.exists(local_archive_path) or os.path.getsize(local_archive_path) == 0:
            err_msg = f"فشل تحميل الأرشيف أو الملف غير موجود/فارغ: {local_archive_path}"
            logger.error(f"Task {task_id}: {err_msg}")
            fn = os.path.basename(str(local_archive_path)) if local_archive_path else "غير معروف"
            raise FileNotFoundError(f"فشل تحميل الأرشيف. الملف '{fn}' غير موجود أو فارغ بعد محاولة التحميل.")

        logger.info(f"Task {task_id}: Download complete. File: {local_archive_path}")
        file_size_mb = os.path.getsize(local_archive_path) / (1024 * 1024)
        logger.info(f"Task {task_id}: تم تحميل الأرشيف: {local_archive_path} (الحجم: {file_size_mb:.2f} MB)")

        archive_type = get_archive_type(local_archive_path)
        logger.info(f"Task {task_id}: تم تحديد نوع الأرشيف: {archive_type} للملف {local_archive_path}")

        if not archive_type:
            original_filename_from_url = os.path.basename(urlparse(original_archive_url).path.split('?')[0])
            if original_filename_from_url and '.' in original_filename_from_url:
                potential_new_path = os.path.join(temp_session_folder, original_filename_from_url)
                if not os.path.exists(potential_new_path) and local_archive_path != potential_new_path:
                    try:
                        logger.info(f"Task {task_id}: محاولة إعادة تسمية {local_archive_path} إلى {potential_new_path} لتحسين كشف النوع.")
                        shutil.move(local_archive_path, potential_new_path)
                        local_archive_path = potential_new_path
                        archive_type = get_archive_type(local_archive_path)
                        logger.info(f"Task {task_id}: تمت إعادة التسمية إلى {original_filename_from_url}، النوع الجديد المحدد: {archive_type}")
                    except Exception as e_rename:
                        logger.warning(f"Task {task_id}: لم يمكن إعادة تسمية الملف المحمل إلى {original_filename_from_url}: {e_rename}. الاستمرار بالملف الأصلي.")

            if not archive_type:
                err_msg = f"لا يمكن تحديد نوع الأرشيف أو أن الصيغة غير مدعومة. اسم الملف: {os.path.basename(local_archive_path)}"
                logger.error(f"Task {task_id}: {err_msg}")
                update_metadata_status(db_session, url_hash, 'FAILED')
                return {'status': 'FAILURE', 'error': err_msg, 'url_hash': url_hash, 'session_id': session_id}

        logger.info(f"Task {task_id}: جاري فك الضغط كأرشيف {archive_type} إلى {extracted_session_folder}...")
        extraction_start_time = time.time()
        extraction_done = False
        if archive_type == 'rar':
            with rarfile.RarFile(local_archive_path) as rf:
                rf.extractall(path=extracted_session_folder)
            extraction_done = True
        elif archive_type == 'zip':
            with zipfile.ZipFile(local_archive_path, 'r') as zf:
                zf.extractall(path=extracted_session_folder)
            extraction_done = True
        elif archive_type == 'tar':
            with tarfile.open(local_archive_path, 'r:*') as tf:
                tf.extractall(path=extracted_session_folder)
            extraction_done = True
        elif archive_type == '7z':
            with py7zr.SevenZipFile(local_archive_path, mode='r') as szf:
                szf.extractall(path=extracted_session_folder)
            extraction_done = True

        if not extraction_done:
            err_msg = f"فشل فك ضغط الأرشيف لنوع '{archive_type}'. قد تكون المكتبة غير قادرة على التعامل مع هذا الملف المحدد."
            logger.error(f"Task {task_id}: {err_msg}")
            update_metadata_status(db_session, url_hash, 'FAILED')
            return {'status': 'FAILURE', 'error': err_msg, 'url_hash': url_hash, 'session_id': session_id}

        extraction_time = time.time() - extraction_start_time
        logger.info(f"Task {task_id}: تم فك ضغط الأرشيف بنجاح إلى: {extracted_session_folder} in {extraction_time:.2f} seconds.")

        structure_build_start_time = time.time()
        structure = build_file_structure(extracted_session_folder, session_id)
        structure_build_time = time.time() - structure_build_start_time
        logger.info(f"Task {task_id}: File structure built in {structure_build_time:.2f} seconds for session {session_id}.")

        structure_file_path = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id, '.archive_structure.json')
        update_metadata_status(db_session, url_hash, 'COMPLETED', structure_file_path)

        if not structure or not structure.get('children'):
            logger.info(f"Task {task_id}: تم فك الضغط بنجاح ولكن الأرشيف يبدو فارغًا أو الهيكل غير صالح للجلسة {session_id}.")
            return {
                'status': 'SUCCESS',
                'message': 'تمت معالجة الأرشيف، ولكنه فارغ أو لا يحتوي على ملفات يمكن عرضها.',
                'session_id': session_id,
                'url_hash': url_hash,
                'structure': {'name': 'root', 'type': 'directory', 'path': '', 'children': []}
            }

        return {
            'status': 'SUCCESS',
            'message': 'تمت معالجة الملف بنجاح.',
            'session_id': session_id,
            'url_hash': url_hash,
            'structure': structure
        }

    except Exception as e:
        # Update status in database on failure
        update_metadata_status(db_session, url_hash, 'FAILED')
        logger.exception(f"Task {task_id}: خطأ غير متوقع أثناء معالجة الأرشيف للجلسة {session_id}: {e}")
        return {'status': 'FAILURE', 'error': f'حدث خطأ غير متوقع أثناء المعالجة: {str(e)}', 'url_hash': url_hash, 'session_id': session_id}
    finally:
        logger.info(f"Task {task_id}: Running cleanup for temporary download data for session {session_id}")
        cleanup_old_session_data(session_id)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process-archive', methods=['POST'])
def process_archive_route():
    data = request.get_json()
    if not data: return jsonify({'error': 'لم يتم إرسال بيانات JSON.'}), 400
    original_archive_url = data.get('archive_url')
    if not original_archive_url: return jsonify({'error': 'لم يتم توفير رابط ملف الأرشيف.'}), 400

    url_hash = hashlib.md5(original_archive_url.encode('utf-8')).hexdigest()
    logger.info(f"Processing request for URL: {original_archive_url}, Hash: {url_hash}")

    # Check database for existing metadata
    metadata = get_metadata_by_url_hash(db_session, url_hash)
    if metadata and metadata.status == 'COMPLETED':
        cached_session_id = metadata.session_id
        cached_structure_file = metadata.structure_file_path

        logger.info(f"Database hit for hash {url_hash}. Session ID: {cached_session_id}. Structure file: {cached_structure_file}")

        if os.path.exists(cached_structure_file):
            try:
                with open(cached_structure_file, 'r', encoding='utf-8') as f:
                    structure = json.load(f)
                logger.info(f"Successfully loaded cached structure for session {cached_session_id}.")
                return jsonify({
                    'message': 'تمت معالجة الملف بنجاح (من قاعدة البيانات).',
                    'session_id': cached_session_id,
                    'structure': structure,
                    'status': 'COMPLETED_FROM_CACHE'
                }), 200
            except Exception as e:
                logger.error(f"Cache hit, but failed to load structure file {cached_structure_file}: {e}. Re-processing.")
        else:
            logger.warning(f"Cache hit, but structure file {cached_structure_file} not found. Re-processing.")

    session_id = str(int(time.time() * 100000))

    temp_session_folder = os.path.join(TEMP_ARCHIVE_DIR_FLASK_APP, session_id)
    extracted_session_folder = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id)

    try:
        os.makedirs(temp_session_folder, exist_ok=True)
        os.makedirs(extracted_session_folder, exist_ok=True)
    except OSError as e:
        logger.error(f"Error creating session directories for {session_id}: {e}")
        return jsonify({'error': f"فشل في إعداد بيئة المعالجة: {e}"}), 500

    # Create initial metadata entry in database
    create_or_update_metadata(db_session, url_hash, original_archive_url, session_id, status='PENDING')

    logger.info(f"Dispatching Celery task for URL: {original_archive_url}, Session ID: {session_id}, URL Hash: {url_hash}")
    task = process_archive_task.delay(original_archive_url, session_id, url_hash)

    # Update task ID in database
    create_or_update_metadata(db_session, url_hash, original_archive_url, session_id, task_id=task.id, status='PENDING')

    return jsonify({
        'message': 'بدأت معالجة الأرشيف. تحقق من الحالة باستخدام معرّف المهمة.',
        'task_id': task.id,
        'session_id': session_id,
        'status': 'PENDING'
    }), 202

@app.route('/task-status/<task_id>', methods=['GET'])
def task_status_route(task_id):
    logger.debug(f"Checking status for task_id: {task_id}")
    task = celery_app.AsyncResult(task_id)

    response_data = {
        'task_id': task_id,
        'celery_status': task.state
    }

    if task.state == 'PENDING':
        response_data['status'] = 'PENDING'
        response_data['message'] = 'المهمة لا تزال قيد المعالجة أو في الانتظار.'
    elif task.state == 'STARTED':
        response_data['status'] = 'STARTED'
        response_data['message'] = 'بدأت المهمة في المعالجة.'
    elif task.state == 'SUCCESS':
        result = task.result
        if isinstance(result, dict):
            response_data.update(result)

            if result.get('status') == 'SUCCESS':
                response_data['message'] = result.get('message', 'اكتملت المهمة بنجاح.')
                # Database update is now handled in the task itself
                logger.info(f"Task {task_id} (Celery SUCCESS, App SUCCESS).")

            elif result.get('status') == 'FAILURE':
                response_data['error'] = result.get('error', 'فشلت المهمة بسبب خطأ منطقي.')
                logger.error(f"Task {task_id} (Celery SUCCESS, App FAILURE). Error: {result.get('error')}")

        else:
            response_data['status'] = 'ERROR_UNEXPECTED_RESULT'
            response_data['error'] = 'اكتملت المهمة ولكن النتيجة غير متوقعة.'
            logger.error(f"Task {task_id} (Celery SUCCESS) but returned unexpected result type: {type(result)}. Result: {result}")

    elif task.state == 'FAILURE':
        response_data['status'] = 'FAILURE'
        error_info = str(task.info) if task.info else "فشل غير معروف في تنفيذ المهمة."
        response_data['error'] = f"فشلت المهمة في التنفيذ: {error_info}"
        logger.error(f"Task {task_id} (Celery FAILURE). State: {task.state}, Info: {task.info}")

    else:
        response_data['status'] = task.state
        response_data['message'] = f'حالة المهمة: {task.state}.'
        logger.info(f"Task {task_id} in unhandled Celery state: {task.state}")

    return jsonify(response_data)

@app.route('/view-file/<session_id>/<path:filepath>')
def view_file(session_id, filepath):
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        logger.warning(f"Potential path traversal in session_id: {session_id}")
        return "معرف جلسة غير صالح.", 400
    # Allow ".." in filepath for legitimate subdirectories,
    # the absolute path resolution and startswith check below are the primary security measures.

    secure_base_path = os.path.abspath(os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id))
    requested_file_path_abs = os.path.abspath(os.path.join(secure_base_path, filepath))

    logger.info(f"Request to view file: session={session_id}, filepath={filepath}")
    logger.debug(f"Secure base path: {secure_base_path}")
    logger.debug(f"Requested absolute path: {requested_file_path_abs}")

    if not requested_file_path_abs.startswith(secure_base_path + os.sep) and requested_file_path_abs != secure_base_path:
        logger.warning(f"Path traversal attempt denied: '{filepath}' resolved to '{requested_file_path_abs}', which is outside '{secure_base_path}'")
        return "الوصول مرفوض (محاولة تجاوز المسار).", 403

    if not os.path.exists(requested_file_path_abs):
        logger.error(f"File not found for viewing: {requested_file_path_abs}")
        return "الملف غير موجود.", 404

    if not os.path.isfile(requested_file_path_abs):
        logger.error(f"Requested path is not a file: {requested_file_path_abs}")
        return "المسار المطلوب ليس ملفًا.", 400

    logger.info(f"Serving file: {filepath} from directory: {secure_base_path}")
    # Offload file serving to front-end web server if configured
    if USE_X_ACCEL_REDIRECT:
        internal_path = f"{INTERNAL_REDIRECT_URL_PREFIX}/{session_id}/{filepath}"
        logger.info(f"Offloading via internal redirect: {internal_path}")
        response = make_response()
        response.headers['X-Accel-Redirect'] = internal_path
        response.headers['Content-Type'] = mimetypes.guess_type(requested_file_path_abs)[0] or 'application/octet-stream'
        return response
    # Default Flask-based serving
    return send_from_directory(secure_base_path, filepath)

@app.teardown_appcontext
def shutdown_session(exception=None):
    """Clean up database session at the end of the request"""
    db_session.remove()

if __name__ == '__main__':
    port = int(os.environ.get("FLASK_RUN_PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
