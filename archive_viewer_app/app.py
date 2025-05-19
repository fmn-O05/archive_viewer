# coding: utf-8
from flask import Flask, request, jsonify, render_template, send_from_directory
import os
import shutil
import time
import glob
import requests
import rarfile # For .rar files
import zipfile # For .zip files
import tarfile # For .tar, .tar.gz, .tar.bz2 files
import py7zr    # For .7z files
import re
import subprocess
import json
import math
import hashlib
import logging # For better logging

# --- إعداد التسجيل ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- الثوابت والمسارات ---
# يفترض أن هذا الملف (app.py) موجود داخل مجلد اسمه مثلاً 'archive_viewer_app'
# وهذا المجلد هو جزء من جذر المشروع على GitHub.
_current_file_dir = os.path.dirname(os.path.abspath(__file__))
BASE_APP_DIR = _current_file_dir # المسار إلى مجلد 'archive_viewer_app'

TEMPLATE_DIR = os.path.join(BASE_APP_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_APP_DIR, 'static')

# مجلدات التحميل ستكون داخل BASE_APP_DIR (أي داخل archive_viewer_app)
UPLOAD_DIR_FLASK_APP = os.path.join(BASE_APP_DIR, 'uploads_flask')
TEMP_ARCHIVE_DIR_FLASK_APP = os.path.join(UPLOAD_DIR_FLASK_APP, 'temp_archive')
EXTRACTED_FILES_DIR_FLASK_APP = os.path.join(UPLOAD_DIR_FLASK_APP, 'extracted_files')

# مسار أداة megadl (يفترض أنها ستكون في PATH عند التشغيل في Colab بعد التثبيت)
MEGADL_EXEC_PATH = "megadl"
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg')

# إنشاء المجلدات إذا لم تكن موجودة (مهم عند التشغيل لأول مرة)
for dir_path in [TEMPLATE_DIR, STATIC_DIR, UPLOAD_DIR_FLASK_APP, TEMP_ARCHIVE_DIR_FLASK_APP, EXTRACTED_FILES_DIR_FLASK_APP]:
    os.makedirs(dir_path, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# --- ذاكرة التخزين المؤقت للروابط المعالجة ---
url_cache = {}
CACHE_FILENAME = os.path.join(UPLOAD_DIR_FLASK_APP, '.url_cache.json') # ملف لتخزين الكاش

def load_cache():
    global url_cache
    if os.path.exists(CACHE_FILENAME):
        try:
            with open(CACHE_FILENAME, 'r', encoding='utf-8') as f:
                url_cache = json.load(f)
            logger.info(f"تم تحميل ذاكرة التخزين المؤقت للروابط من {CACHE_FILENAME}")
        except Exception as e:
            logger.error(f"خطأ في تحميل ذاكرة التخزين المؤقت: {e}")
            url_cache = {}
    else:
        url_cache = {}

def save_cache():
    try:
        with open(CACHE_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(url_cache, f, ensure_ascii=False, indent=2)
        logger.info(f"تم حفظ ذاكرة التخزين المؤقت للروابط في {CACHE_FILENAME}")
    except Exception as e:
        logger.error(f"خطأ في حفظ ذاكرة التخزين المؤقت: {e}")

load_cache() # تحميل الكاش عند بدء تشغيل التطبيق

# --- دوال مساعدة ---
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
    logger.info(f"محاولة تنظيف بيانات الجلسة للمعرّف: {session_id}")
    temp_session_folder = os.path.join(TEMP_ARCHIVE_DIR_FLASK_APP, session_id)
    # extracted_session_folder = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id) # لا تحذف المستخرجة للاستفادة من الكاش

    try:
        if os.path.exists(temp_session_folder):
            shutil.rmtree(temp_session_folder)
            logger.info(f"تم حذف مجلد الأرشيف المؤقت: {temp_session_folder}")
    except Exception as e:
        logger.error(f"خطأ أثناء حذف المجلد المؤقت {temp_session_folder}: {e}")
    pass

def build_file_structure(start_path, session_id):
    """ بناء هيكل هرمي للملفات والمجلدات. """
    structure = {'name': 'root', 'type': 'directory', 'path': '', 'children': []}
    structure_file_path = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id, '.archive_structure.json')

    if os.path.exists(structure_file_path):
        try:
            with open(structure_file_path, 'r', encoding='utf-8') as f:
                logger.info(f"تحميل الهيكل من الملف الم缓存: {structure_file_path}")
                return json.load(f)
        except Exception as e:
            logger.error(f"خطأ في تحميل ملف الهيكل الم缓存 {structure_file_path}: {e}. إعادة البناء.")

    dir_map = {start_path: structure}

    for root, dirs, files in os.walk(start_path):
        rel_root = os.path.relpath(root, start_path)
        if rel_root == '.': rel_root = ''

        parent_node = structure
        if rel_root:
            path_parts = rel_root.split(os.sep)
            current_search_node = structure
            found = True
            for part in path_parts:
                child_node = next((c for c in current_search_node['children'] if c['name'] == part and c['type'] == 'directory'), None)
                if child_node:
                    current_search_node = child_node
                else:
                    found = False; break
            if found:
                 parent_node = current_search_node
            else:
                logger.error(f"لم يتم العثور على العقدة الأصل لـ {rel_root}. تخطي العناصر في هذا المسار.")
                continue

        for d_name in sorted(dirs):
            dir_path_abs = os.path.join(root, d_name)
            dir_path_rel = os.path.relpath(dir_path_abs, start_path).replace(os.sep, '/')
            dir_node = {'name': d_name, 'type': 'directory', 'path': dir_path_rel, 'children': []}
            parent_node['children'].append(dir_node)

        for f_name in sorted(files):
            if f_name == '.archive_structure.json': continue
            file_path_abs = os.path.join(root, f_name)
            file_path_rel = os.path.relpath(file_path_abs, start_path).replace(os.sep, '/')
            is_image = f_name.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
            file_node = {'name': f_name, 'type': 'file', 'path': file_path_rel, 'is_image': is_image}
            parent_node['children'].append(file_node)

    try:
        with open(structure_file_path, 'w', encoding='utf-8') as f:
            json.dump(structure, f, ensure_ascii=False, indent=2)
        logger.info(f"تم حفظ الهيكل الجديد في ملف الكاش: {structure_file_path}")
    except Exception as e:
        logger.error(f"خطأ في حفظ ملف الهيكل {structure_file_path}: {e}")

    return structure

def get_archive_type(filepath):
    filename = os.path.basename(filepath).lower()
    # التحقق من نوع الملف باستخدام المكتبات المتخصصة أولاً
    try: # rarfile.is_rarfile يمكن أن يلقي استثناءات إذا كان الملف غير موجود أو لا يمكن الوصول إليه
        if os.path.exists(filepath) and rarfile.is_rarfile(filepath): return 'rar'
    except Exception: pass
    try:
        if os.path.exists(filepath) and zipfile.is_zipfile(filepath): return 'zip'
    except Exception: pass
    try:
        if os.path.exists(filepath) and tarfile.is_tarfile(filepath): return 'tar'
    except Exception: pass
    try:
        if os.path.exists(filepath) and filename.endswith('.7z'):
            with py7zr.SevenZipFile(filepath, 'r') as _:
                return '7z'
    except Exception: pass

    # التحقق بناءً على الامتداد كحل أخير
    if filename.endswith('.rar'): return 'rar'
    if filename.endswith('.zip'): return 'zip'
    if filename.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')): return 'tar'
    return None

# --- مسارات Flask ---
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
    logger.info(f"معالجة الرابط: {original_archive_url}, Hash: {url_hash}")

    if url_hash in url_cache:
        cached_data = url_cache[url_hash]
        cached_session_id = cached_data.get('session_id')
        # تأكد أن المسار إلى ملف الهيكل صحيح
        cached_structure_file = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, cached_session_id, '.archive_structure.json')
        logger.info(f"العثور على Hash في الكاش {url_hash}. Session ID: {cached_session_id}. مسار ملف الهيكل: {cached_structure_file}")

        if os.path.exists(cached_structure_file):
            try:
                with open(cached_structure_file, 'r', encoding='utf-8') as f:
                    structure = json.load(f)
                logger.info(f"تم تحميل الهيكل الم缓存 بنجاح للجلسة {cached_session_id}.")
                return jsonify({
                    'message': 'تمت معالجة الملف بنجاح (من ذاكرة التخزين المؤقت).',
                    'session_id': cached_session_id,
                    'structure': structure
                }), 200
            except Exception as e:
                logger.error(f"تم العثور على Hash، ولكن فشل تحميل ملف الهيكل {cached_structure_file}: {e}. إعادة المعالجة.")
        else:
            logger.warning(f"تم العثور على Hash، ولكن ملف الهيكل {cached_structure_file} غير موجود. إعادة المعالجة.")
    else:
        logger.info(f"لم يتم العثور على Hash في الكاش {url_hash}. معالجة طلب جديد.")

    session_id = str(int(time.time() * 1000))
    temp_session_folder = os.path.join(TEMP_ARCHIVE_DIR_FLASK_APP, session_id)
    extracted_session_folder = os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id)
    os.makedirs(temp_session_folder, exist_ok=True)
    os.makedirs(extracted_session_folder, exist_ok=True)

    local_archive_filename = "archive_download" # اسم عام، قد يتغير بعد التحميل
    local_archive_path = os.path.join(temp_session_folder, local_archive_filename)
    download_successful = False
    actual_downloaded_filename_for_type_check = local_archive_filename

    try:
        logger.info(f"بدء التحميل للجلسة {session_id} من {original_archive_url}")
        if "mega.nz" in original_archive_url or "mega.co.nz" in original_archive_url:
            logger.info(f"رابط MEGA. استخدام MEGADL_EXEC_PATH: {MEGADL_EXEC_PATH}")
            if not MEGADL_EXEC_PATH or (MEGADL_EXEC_PATH == "megadl" and not shutil.which("megadl")) and not os.path.exists(MEGADL_EXEC_PATH):
                 raise FileNotFoundError(f"أداة megadl غير موجودة أو غير قابلة للتنفيذ: {MEGADL_EXEC_PATH}")

            megadl_command = [MEGADL_EXEC_PATH, original_archive_url, "--path", temp_session_folder]
            process = subprocess.run(megadl_command, capture_output=True, text=True, check=False, timeout=600)
            logger.info(f"megadl stdout: {process.stdout}")
            logger.error(f"megadl stderr: {process.stderr}")
            if process.returncode == 0:
                downloaded_files_mega = os.listdir(temp_session_folder)
                if downloaded_files_mega:
                    actual_downloaded_filename_for_type_check = downloaded_files_mega[0]
                    local_archive_path = os.path.join(temp_session_folder, actual_downloaded_filename_for_type_check)
                    download_successful = True
                    logger.info(f"تم تحميل MEGA بنجاح: {local_archive_path}")
                else: raise Exception("megadl انتهى ولكن لم يتم العثور على ملف في المجلد المؤقت.")
            else: raise Exception(f"فشل megadl. الرمز: {process.returncode}. الخطأ: {process.stderr or process.stdout}")

        elif "drive.google.com" in original_archive_url:
            download_url = get_google_drive_direct_link(original_archive_url)
            logger.info(f"رابط Google Drive. الرابط المباشر: {download_url}")
            headers = {'User-Agent': 'Mozilla/5.0'}
            with requests.get(download_url, headers=headers, stream=True, timeout=(15, 300), allow_redirects=True) as r:
                r.raise_for_status()
                # محاولة الحصول على اسم الملف من Google Drive (قد لا يكون دقيقًا دائمًا)
                content_disposition = r.headers.get('content-disposition')
                if content_disposition:
                    fname_match = re.findall('filename="?([^"]+)"?', content_disposition)
                    if fname_match:
                        actual_downloaded_filename_for_type_check = fname_match[0]
                        local_archive_path = os.path.join(temp_session_folder, actual_downloaded_filename_for_type_check)

                with open(local_archive_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            download_successful = True
            logger.info(f"تم تحميل Google Drive بنجاح: {local_archive_path}")
        else: # رابط مباشر
            download_url = original_archive_url
            logger.info(f"تحميل رابط مباشر: {download_url}")
            headers = {'User-Agent': 'Mozilla/5.0'}
            with requests.get(download_url, headers=headers, stream=True, timeout=(15, 300), allow_redirects=True) as r:
                r.raise_for_status()
                content_disposition = r.headers.get('content-disposition')
                if content_disposition:
                    fname_match = re.findall('filename="?([^"]+)"?', content_disposition)
                    if fname_match:
                        actual_downloaded_filename_for_type_check = fname_match[0]
                        local_archive_path = os.path.join(temp_session_folder, actual_downloaded_filename_for_type_check)
                else: # إذا لم يكن هناك content-disposition، استخدم اسم الملف من الرابط
                    url_filename = os.path.basename(requests.utils.urlparse(download_url).path)
                    if url_filename:
                         actual_downloaded_filename_for_type_check = url_filename
                         local_archive_path = os.path.join(temp_session_folder, actual_downloaded_filename_for_type_check)


                with open(local_archive_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            download_successful = True
            logger.info(f"تم التحميل المباشر بنجاح: {local_archive_path}")

        if not download_successful or not os.path.exists(local_archive_path):
            raise FileNotFoundError(f"فشل تحميل الأرشيف أو الملف غير موجود: {local_archive_path}")

        file_size_mb = os.path.getsize(local_archive_path) / (1024 * 1024)
        logger.info(f"تم تحميل الأرشيف: {local_archive_path} (الحجم: {file_size_mb:.2f} MB)")

        archive_type = get_archive_type(local_archive_path)
        logger.info(f"تم تحديد نوع الأرشيف: {archive_type} للملف {local_archive_path}")

        if not archive_type:
            original_filename_from_url = os.path.basename(original_archive_url.split('?')[0])
            if '.' in original_filename_from_url:
                potential_new_path = os.path.join(temp_session_folder, original_filename_from_url)
                if not os.path.exists(potential_new_path) and local_archive_path != potential_new_path :
                    try:
                        shutil.move(local_archive_path, potential_new_path) # استخدام shutil.move
                        local_archive_path = potential_new_path
                        archive_type = get_archive_type(local_archive_path)
                        logger.info(f"تمت إعادة التسمية إلى {original_filename_from_url}، النوع الجديد المحدد: {archive_type}")
                    except Exception as e_rename:
                        logger.warning(f"لم يمكن إعادة تسمية الملف المحمل إلى {original_filename_from_url}: {e_rename}")
            if not archive_type:
                 return jsonify({'error': f"لا يمكن تحديد نوع الأرشيف أو أن الصيغة غير مدعومة. اسم الملف: {os.path.basename(local_archive_path)}"}), 400

        logger.info(f"جاري فك الضغط كأرشيف {archive_type}...")
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
            os.makedirs(extracted_session_folder, exist_ok=True)
            with py7zr.SevenZipFile(local_archive_path, mode='r') as szf:
                szf.extractall(path=extracted_session_folder)
            extraction_done = True

        if not extraction_done:
             return jsonify({'error': "فشل فك ضغط الأرشيف."}), 500
        logger.info(f"تم فك ضغط الأرشيف بنجاح إلى: {extracted_session_folder}")

        structure = build_file_structure(extracted_session_folder, session_id)
        if not structure or not structure.get('children'):
            logger.info(f"تم فك الضغط بنجاح ولكن الأرشيف يبدو فارغًا أو الهيكل غير صالح للجلسة {session_id}.")
            url_cache[url_hash] = {'session_id': session_id, 'structure_file': os.path.join(extracted_session_folder, '.archive_structure.json')}
            save_cache()
            return jsonify({
                'message': 'تمت معالجة الأرشيف، ولكنه فارغ أو لا يحتوي على ملفات يمكن عرضها.',
                'session_id': session_id,
                'structure': {'name': 'root', 'type': 'directory', 'path': '', 'children': []}
            }), 200

        url_cache[url_hash] = {'session_id': session_id, 'structure_file': os.path.join(extracted_session_folder, '.archive_structure.json')}
        save_cache()

        return jsonify({
            'message': 'تمت معالجة الملف بنجاح.',
            'session_id': session_id,
            'structure': structure
        }), 200

    except subprocess.TimeoutExpired as e_sub_timeout:
        logger.error(f"انتهت مهلة العملية الفرعية (مثل megadl) للجلسة {session_id}: {e_sub_timeout}")
        return jsonify({'error': 'فشل تحميل الملف: انتهت مهلة العملية الخارجية.'}), 500
    except requests.exceptions.Timeout as e_req_timeout:
        logger.error(f"انتهت مهلة الطلب للجلسة {session_id}: {e_req_timeout}")
        return jsonify({'error': 'فشل تحميل الملف: انتهت مهلة الاتصال بالرابط.'}), 500
    except requests.exceptions.HTTPError as e_http:
        logger.error(f"خطأ HTTP للجلسة {session_id}: {e_http}")
        return jsonify({'error': f'فشل تحميل الملف: خطأ HTTP {e_http.response.status_code}. URL: {e_http.request.url}'}), 500
    except requests.exceptions.RequestException as e_req:
        logger.error(f"استثناء طلب للجلسة {session_id}: {e_req}")
        return jsonify({'error': f'فشل تحميل الملف: {str(e_req)}.'}), 500
    except (rarfile.BadRarFile, rarfile.NeedFirstVolume, zipfile.BadZipFile, tarfile.TarError, py7zr.exceptions.Bad7zFile) as e_bad_archive:
        logger.error(f"ملف أرشيف تالف للجلسة {session_id}: {e_bad_archive}")
        return jsonify({'error': f'فشل فك ضغط الملف: الملف تالف أو ليس بصيغة مدعومة بشكل كامل. ({archive_type if "archive_type" in locals() else "غير معروف"}) النوع الخطأ: {type(e_bad_archive).__name__}'}), 500
    except FileNotFoundError as e_fnf:
        logger.error(f"لم يتم العثور على الملف أثناء المعالجة للجلسة {session_id}: {e_fnf}")
        return jsonify({'error': f'خطأ في الملفات: {str(e_fnf)}'}), 500
    except Exception as e:
        logger.exception(f"خطأ غير متوقع أثناء معالجة الأرشيف للجلسة {session_id}: {e}")
        return jsonify({'error': f'حدث خطأ غير متوقع أثناء المعالجة: {str(e)}'}), 500
    finally:
        if 'session_id' in locals() and session_id:
            cleanup_old_session_data(session_id)


@app.route('/view-file/<session_id>/<path:filepath>')
def view_file(session_id, filepath):
    secure_base_path = os.path.abspath(os.path.join(EXTRACTED_FILES_DIR_FLASK_APP, session_id))
    requested_file_path_abs = os.path.normpath(os.path.join(secure_base_path, filepath))

    logger.info(f"طلب عرض ملف: session={session_id}, filepath={filepath}, secure_base={secure_base_path}, requested_abs={requested_file_path_abs}")

    if not requested_file_path_abs.startswith(secure_base_path + os.sep) and requested_file_path_abs != secure_base_path :
        logger.warning(f"تم رفض محاولة تجاوز المسار: {filepath} تم حلها إلى {requested_file_path_abs} وهو خارج {secure_base_path}")
        return "الوصول مرفوض (Path Traversal).", 403

    if not os.path.exists(requested_file_path_abs) or not os.path.isfile(requested_file_path_abs):
        logger.error(f"لم يتم العثور على الملف للعرض: {requested_file_path_abs}")
        return "الملف غير موجود.", 404

    logger.info(f"خدمة الملف: {filepath} من المجلد: {secure_base_path}")
    return send_from_directory(secure_base_path, filepath)

if __name__ == '__main__':
    # عند التشغيل محليًا، يمكنك تغيير المنفذ أو تفعيل وضع التصحيح
    # debug=True ليس موصى به للإنتاج أو عند استخدام ngrok بشكل مستمر في Colab
    # use_reloader=False مهم لـ ngrok في Colab
    default_port = int(os.environ.get("PORT", 5000)) # PORT لبعض بيئات الاستضافة
    app.run(host='0.0.0.0', port=default_port, debug=False, use_reloader=False)
