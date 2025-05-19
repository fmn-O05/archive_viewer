document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM fully loaded. SCRIPT_VERSION_PROFESSIONAL_005_RTL_FIXES");

    // --- DOM Element Variables ---
    const archiveUrlForm = document.getElementById('archive-url-form');
    const archiveUrlInput = document.getElementById('archive-url');
    const resultsSection = document.getElementById('results-section');
    const loadingIndicator = document.getElementById('loading-indicator');
    const loadingStatusText = document.getElementById('loading-status-text');
    const progressBarContainer = document.getElementById('progress-bar-container');
    const progressBar = document.getElementById('progress-bar');
    const errorMessageSection = document.getElementById('error-message-section');
    const errorMessageText = document.getElementById('error-message-text');

    const treeViewContainer = document.getElementById('tree-view-container');
    const breadcrumbContainer = document.getElementById('breadcrumb-container');
    // const fileDetailsView = document.getElementById('file-details-view'); // Not directly used for now

    const fileListDiv = document.getElementById('file-list');
    const fileListInfoDiv = document.getElementById('file-list-info');
    const paginationControlsDiv = document.getElementById('pagination-controls');
    const imageDisplayDiv = document.getElementById('image-display');
    const imageListInfoDiv = document.getElementById('image-list-info');

    const imageModal = document.getElementById('imageModal');
    const modalImage = document.getElementById('modalImage');
    const modalImageName = document.getElementById('modalImageName');
    const modalCloseButton = document.getElementById('modalCloseButton');
    const modalPrevButton = document.getElementById('modalPrevButton');
    const modalNextButton = document.getElementById('modalNextButton');
    const modalImageCounter = document.getElementById('modalImageCounter');
    const modalSpinner = document.getElementById('modalSpinner');

    const tabAllFilesButton = document.getElementById('tab-all-files');
    const tabImagesOnlyButton = document.getElementById('tab-images-only');
    const tabContentAllFiles = document.getElementById('tab-content-all-files');
    const tabContentImagesOnly = document.getElementById('tab-content-images-only');

    // --- Theme Toggle Elements and Logic ---
    const htmlEl = document.documentElement;
    const themeToggleButton = document.getElementById('theme-toggle');
    const sunIcon = themeToggleButton ? themeToggleButton.querySelector('.fa-sun') : null;
    const moonIcon = themeToggleButton ? themeToggleButton.querySelector('.fa-moon') : null;

    function updateThemeIcons(theme) {
        if (!sunIcon || !moonIcon) return;
        if (theme === 'dark') {
            htmlEl.classList.add('dark'); // Ensure dark class is on html
            htmlEl.classList.remove('light');
            sunIcon.classList.add('hidden');
            moonIcon.classList.remove('hidden');
        } else { // light theme
            htmlEl.classList.remove('dark');
            htmlEl.classList.add('light'); // Ensure light class is on html
            sunIcon.classList.remove('hidden');
            moonIcon.classList.add('hidden');
        }
    }

    function applyTheme(theme, fromUserAction = false) {
        updateThemeIcons(theme);
        if (fromUserAction) {
            localStorage.setItem('theme', theme);
        }
    }

    if (themeToggleButton) {
        themeToggleButton.addEventListener('click', () => {
            const newTheme = htmlEl.classList.contains('dark') ? 'light' : 'dark';
            applyTheme(newTheme, true);
        });
    }
    const preferredTheme = localStorage.getItem('theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    applyTheme(preferredTheme, false);


    // --- State Variables ---
    let currentSessionId = null;
    let currentPageGlobal = 1;
    let filesPerPageGlobal = 20; // عدد العناصر في كل صفحة
    let fullArchiveStructure = null;
    let currentPathInTree = '';
    let currentDisplayedItems = []; // المجلدات والملفات في المسار الحالي المعروض
    let allImagesForModalNavigation = []; // كل الصور في الأرشيف للتنقل في الـ modal
    let currentModalImageIndex = -1;

    const lazyLoadPlaceholder = 'data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACH5BAEAAAAALAAAAAABAAEAAAICRAEAOw==';
    const errorImageSrc = 'https://placehold.co/400x300/fecaca/991b1b?text=Image+Error';


    // --- Helper Functions ---
    function hideError() {
        if(errorMessageSection) errorMessageSection.classList.add('hidden');
        if(errorMessageText) errorMessageText.textContent = '';
    }
    function showError(message) {
        console.error("Showing error:", message);
        if(errorMessageText && errorMessageSection) {
            errorMessageText.textContent = message;
            errorMessageSection.classList.remove('hidden');
        }
        if(resultsSection) resultsSection.classList.add('hidden');
        if(treeViewContainer) treeViewContainer.innerHTML = '<p class="text-gray-500 dark:text-gray-400">هيكل المجلدات سيظهر هنا...</p>';
        if(breadcrumbContainer) breadcrumbContainer.innerHTML = '';
        if(fileListDiv) fileListDiv.innerHTML = '<p class="text-gray-500 dark:text-gray-400">اختر مجلدًا...</p>';
        if(imageDisplayDiv) imageDisplayDiv.innerHTML = '<p class="text-gray-500 dark:text-gray-400 col-span-full">اختر مجلدًا...</p>';
        if(paginationControlsDiv) paginationControlsDiv.innerHTML = '';
        if(fileListInfoDiv) fileListInfoDiv.innerHTML = '';
        if(imageListInfoDiv) imageListInfoDiv.innerHTML = '';
        hideLoading();
    }
    function showLoading(status = "جاري المعالجة...", progress = null) {
        if(loadingIndicator) loadingIndicator.classList.remove('hidden');
        if(loadingStatusText) loadingStatusText.textContent = `⏳ ${status}`;
        if (progress !== null && progressBarContainer && progressBar) {
            progressBarContainer.classList.remove('hidden');
            progressBar.style.width = `${progress}%`;
        } else if (progressBarContainer) {
            progressBarContainer.classList.add('hidden');
        }
        if (status.indexOf("تحميل الصفحة") === -1) { // لا تخفي النتائج عند تحميل صفحة جديدة
             if(resultsSection) resultsSection.classList.add('hidden');
        }
        hideError();
    }
    function hideLoading() {
        if(loadingIndicator) loadingIndicator.classList.add('hidden');
        if(progressBarContainer) progressBarContainer.classList.add('hidden');
        if(progressBar) progressBar.style.width = '0%';
    }
    function getFileIconClass(filename, isDirectory = false) {
        if (isDirectory) return 'fas fa-folder text-sky-500 dark:text-sky-400';
        const extension = filename.split('.').pop().toLowerCase();
        switch (extension) {
            case 'pdf': return 'fas fa-file-pdf text-red-500 dark:text-red-400';
            case 'doc': case 'docx': return 'fas fa-file-word text-blue-500 dark:text-blue-400';
            case 'xls': case 'xlsx': return 'fas fa-file-excel text-green-500 dark:text-green-400';
            case 'ppt': case 'pptx': return 'fas fa-file-powerpoint text-orange-500 dark:text-orange-400';
            case 'zip': case 'rar': case '7z': case 'tar': case 'gz': case 'bz2': return 'fas fa-file-archive text-yellow-500 dark:text-yellow-400';
            case 'txt': case 'log': return 'fas fa-file-alt text-gray-500 dark:text-gray-400';
            case 'jpg': case 'jpeg': case 'png': case 'gif': case 'bmp': case 'webp': case 'svg': return 'fas fa-file-image text-purple-500 dark:text-purple-400';
            case 'mp3': case 'wav': case 'ogg': return 'fas fa-file-audio text-teal-500 dark:text-teal-400';
            case 'mp4': case 'mov': case 'avi': case 'mkv': return 'fas fa-file-video text-pink-500 dark:text-pink-400';
            case 'js': case 'json': case 'py': case 'html': case 'css': case 'sh': return 'fas fa-file-code text-indigo-500 dark:text-indigo-400';
            default: return 'fas fa-file text-gray-400 dark:text-gray-500';
        }
    }

    // --- Tree View Functions ---
    function buildTree(node, pathPrefix = '') {
        const ul = document.createElement('ul');

        (node.children || []).forEach(child => {
            const li = document.createElement('li');
            li.className = 'tree-item my-1';
            if (child.type === 'directory' && child.children && child.children.length > 0) {
                 li.classList.add('collapsed');
            } else if (child.type === 'directory') {
                 li.classList.add('collapsed'); // Empty folders also
            }

            const label = document.createElement('span');
            label.className = 'tree-label p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 cursor-pointer flex items-center text-sm';
            label.dataset.path = (pathPrefix ? pathPrefix + '/' : '') + child.name;
            label.dataset.type = child.type;

            if (child.type === 'directory') {
                const toggler = document.createElement('span');
                toggler.className = 'tree-toggler text-xs text-gray-500 dark:text-gray-400';
                if (!child.children || child.children.length === 0) {
                    toggler.style.visibility = 'hidden';
                }
                label.appendChild(toggler); // Toggler first
            }

            const icon = document.createElement('i');
            icon.className = getFileIconClass(child.name, child.type === 'directory');
            label.appendChild(icon); // Icon second

            label.appendChild(document.createTextNode(child.name)); // Text last

            li.appendChild(label);

            label.addEventListener('click', (event) => {
                event.stopPropagation(); // منع انتشار الحدث إلى العناصر الأصلية
                if (child.type === 'directory') { // Toggle and navigate for directories
                    if (child.children && child.children.length > 0) {
                        li.classList.toggle('expanded');
                        li.classList.toggle('collapsed');
                    }
                }
                navigateToPath(label.dataset.path); // Navigate for both files and folders

                document.querySelectorAll('.tree-label.selected').forEach(el => el.classList.remove('selected', 'bg-sky-100', 'dark:bg-sky-700', 'font-semibold'));
                label.classList.add('selected', 'bg-sky-100', 'dark:bg-sky-700', 'font-semibold');
            });

            if (child.type === 'directory' && child.children && child.children.length > 0) {
                li.appendChild(buildTree(child, label.dataset.path));
            }
            ul.appendChild(li);
        });
        return ul;
    }

    function renderTree(structure) {
        if (!treeViewContainer) return;
        treeViewContainer.innerHTML = '';
        if (!structure || !structure.children || structure.children.length === 0) {
            treeViewContainer.innerHTML = '<p class="text-gray-500 dark:text-gray-400 p-2">الأرشيف فارغ أو لا يمكن قراءة هيكله.</p>';
            return;
        }
        const treeRoot = buildTree(structure);
        treeViewContainer.appendChild(treeRoot);
    }


    // --- Breadcrumb Functions ---
    function renderBreadcrumbs(path) {
        if (!breadcrumbContainer) return;
        breadcrumbContainer.innerHTML = '';
        const parts = path.split('/').filter(p => p);

        const rootLinkSpan = document.createElement('span');
        rootLinkSpan.className = 'breadcrumb-item';
        const rootAnchor = document.createElement('a');
        rootAnchor.href = '#';
        rootAnchor.textContent = 'الجذر';
        rootAnchor.dataset.path = '';
        rootAnchor.addEventListener('click', (e) => { e.preventDefault(); navigateToPath(''); });
        rootLinkSpan.appendChild(rootAnchor);
        breadcrumbContainer.appendChild(rootLinkSpan);

        let currentBuiltPath = '';
        parts.forEach(part => {
            currentBuiltPath += (currentBuiltPath ? '/' : '') + part;
            const partSpan = document.createElement('span');
            partSpan.className = 'breadcrumb-item'; // CSS will add '/' before this for RTL
            const partAnchor = document.createElement('a');
            partAnchor.href = '#';
            partAnchor.textContent = part;
            partAnchor.dataset.path = currentBuiltPath;
            partAnchor.addEventListener('click', (e) => { e.preventDefault(); navigateToPath(currentBuiltPath); });
            partSpan.appendChild(partAnchor);
            breadcrumbContainer.appendChild(partSpan);
        });
    }


    // --- Navigation and Data Fetching for Current Path ---
    function getItemsForPath(path, structure) {
        if (!structure) return { files: [], folders: [], allChildren: [] };
        let currentNode = structure;
        if (path) {
            const parts = path.split('/');
            for (const part of parts) {
                if (!part) continue;
                const foundNode = (currentNode.children || []).find(c => c.name === part && c.type === 'directory');
                if (foundNode) {
                    currentNode = foundNode;
                } else { // Path part not found, likely means it's an invalid path or structure issue
                    return { files: [], folders: [], allChildren: [] };
                }
            }
        }
        // Ensure children is always an array
        const childrenOfCurrentNode = currentNode.children || [];
        const files = childrenOfCurrentNode.filter(c => c.type === 'file');
        const folders = childrenOfCurrentNode.filter(c => c.type === 'directory');
        return { files, folders, allChildren: childrenOfCurrentNode };
    }


    function navigateToPath(path) {
        currentPathInTree = path;
        renderBreadcrumbs(path);
        const { files, folders, allChildren } = getItemsForPath(path, fullArchiveStructure);

        // currentDisplayedItems should contain both folders and files for the current path
        currentDisplayedItems = [...folders, ...files]; // Folders first, then files

        currentPageGlobal = 1; // Reset to first page for new path
        displayCurrentPathItemsPage();

        // Highlight selected item in tree
        document.querySelectorAll('.tree-label.selected').forEach(el => el.classList.remove('selected', 'bg-sky-100', 'dark:bg-sky-700', 'font-semibold'));
        const treeLabels = treeViewContainer.querySelectorAll('.tree-label');
        treeLabels.forEach(label => {
            if (label.dataset.path === path) {
                label.classList.add('selected', 'bg-sky-100', 'dark:bg-sky-700', 'font-semibold');
                // Expand parent nodes if this is a deep link or direct navigation
                let currentElem = label.parentElement; // Start from LI
                while(currentElem && currentElem !== treeViewContainer) {
                    if (currentElem.tagName === 'LI' && currentElem.classList.contains('tree-item') && currentElem.classList.contains('collapsed')) {
                        currentElem.classList.remove('collapsed');
                        currentElem.classList.add('expanded');
                    }
                    currentElem = currentElem.parentElement.parentElement; // Go up to parent LI
                }
            }
        });
         // Switch to 'all-files' tab by default when navigating to a new path
        switchTab('all-files');
    }

    function displayCurrentPathItemsPage() {
        const totalItems = currentDisplayedItems.length;
        const totalPages = Math.ceil(totalItems / filesPerPageGlobal);
        if (currentPageGlobal > totalPages && totalPages > 0) currentPageGlobal = totalPages;
        if (currentPageGlobal < 1 && totalItems > 0) currentPageGlobal = 1;
        else if (totalItems === 0) currentPageGlobal = 1;


        const startIndex = (currentPageGlobal - 1) * filesPerPageGlobal;
        const endIndex = startIndex + filesPerPageGlobal;
        const pageItems = currentDisplayedItems.slice(startIndex, endIndex);

        displayItemsInList(pageItems, currentSessionId, currentPathInTree); // Pass current path
        renderPaginationControlsForCurrentPath(currentPageGlobal, totalPages, totalItems, filesPerPageGlobal);
        updateImageListInfoForCurrentPath(pageItems, totalItems); // totalItems is for the current path
    }


    // --- Form Submission ---
    if (archiveUrlForm) {
        archiveUrlForm.addEventListener('submit', async function(event) {
            event.preventDefault();
            showLoading("بدء المعالجة...");
            currentSessionId = null;
            fullArchiveStructure = null;
            currentPathInTree = '';
            allImagesForModalNavigation = []; // Reset global image list for modal

            const archiveUrl = archiveUrlInput.value.trim();
            if (!archiveUrl) { showError('الرجاء إدخال رابط ملف الأرشيف.'); return; }

            try {
                showLoading("جاري تنزيل الأرشيف...", 0);

                const response = await fetch('/process-archive', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ archive_url: archiveUrl }),
                });

                hideLoading();
                if (!response.ok) {
                    let errorData;
                    try { errorData = await response.json(); } catch (e) { /* no json */ }
                    const errorMessage = errorData && errorData.error ? errorData.error : `حدث خطأ في الخادم: ${response.status} ${response.statusText}`;
                    throw new Error(errorMessage);
                }
                const data = await response.json();
                if (data.error) { showError(data.error); return; }

                resultsSection.classList.remove('hidden');
                currentSessionId = data.session_id;
                fullArchiveStructure = data.structure;

                renderTree(fullArchiveStructure);
                navigateToPath(''); // Navigate to root initially

                // Collect all images from the entire archive for modal navigation
                allImagesForModalNavigation = [];
                function collectAllImagesRecursive(node, pathPrefix = '') {
                    if (!node || !node.children) return;
                    node.children.forEach(child => {
                        const itemPath = (pathPrefix ? pathPrefix + '/' : '') + child.name;
                        if (child.type === 'file' && child.is_image) {
                            allImagesForModalNavigation.push({
                                name: child.name,
                                path: itemPath, // Full path from root
                                downloadUrl: `/view-file/${currentSessionId}/${encodeURIComponent(itemPath)}`
                            });
                        } else if (child.type === 'directory') {
                            collectAllImagesRecursive(child, itemPath);
                        }
                    });
                }
                collectAllImagesRecursive(fullArchiveStructure);


            } catch (error) {
                console.error('Error processing archive URL:', error);
                showError(error.message || 'فشل في معالجة الرابط.');
            }
        });
    }

    // --- Data Display and Pagination Functions ---
    function displayItemsInList(items, sessionId, basePath) { // basePath is currentPathInTree
        if (!fileListDiv || !imageDisplayDiv) return;
        fileListDiv.innerHTML = '';
        imageDisplayDiv.innerHTML = '';

        if (!items || items.length === 0) {
            const msg = '<p class="text-gray-500 dark:text-gray-400 p-4">لا توجد ملفات أو مجلدات لعرضها في هذا الموقع.</p>';
            fileListDiv.innerHTML = msg;
            imageDisplayDiv.innerHTML = `<p class="text-gray-500 dark:text-gray-400 col-span-full p-4">لا توجد صور لعرضها.</p>`;
            return;
        }

        const fileListContainer = document.createElement('div');
        let imagesFoundOnPage = false;

        items.forEach(item => {
            const itemFullPath = item.path;

            const fileItemDiv = document.createElement('div');
            fileItemDiv.className = 'file-item';

            const fileInfoDiv = document.createElement('div');
            fileInfoDiv.className = 'file-info';
            const iconElement = document.createElement('i');
            iconElement.className = getFileIconClass(item.name, item.type === 'directory') + ' fa-lg';
            const fileNameSpan = document.createElement('span');
            fileNameSpan.className = 'file-name';
            fileNameSpan.textContent = item.name;

            if (item.type === 'directory') {
                fileNameSpan.classList.add('cursor-pointer', 'hover:underline');
                fileNameSpan.addEventListener('click', () => navigateToPath(itemFullPath));
                iconElement.classList.add('cursor-pointer');
                iconElement.addEventListener('click', () => navigateToPath(itemFullPath));
            }

            fileInfoDiv.appendChild(iconElement); // Icon
            fileInfoDiv.appendChild(fileNameSpan); // Text
            fileItemDiv.appendChild(fileInfoDiv);

            if (item.type === 'file') {
                const downloadLink = document.createElement('a');
                const downloadUrl = `/view-file/${sessionId}/${encodeURIComponent(itemFullPath)}`;
                downloadLink.href = downloadUrl;
                downloadLink.className = 'download-link';
                downloadLink.setAttribute('download', item.name);
                downloadLink.setAttribute('title', `تحميل ${item.name}`);
                const downloadIcon = document.createElement('i');
                downloadIcon.className = 'fas fa-download';
                downloadLink.appendChild(downloadIcon);
                downloadLink.appendChild(document.createTextNode(' تحميل'));
                fileItemDiv.appendChild(downloadLink);
            }
            fileListContainer.appendChild(fileItemDiv);

            if (item.type === 'file' && item.is_image && sessionId) {
                imagesFoundOnPage = true;
                const imgContainer = document.createElement('div');
                imgContainer.className = 'image-container-class relative aspect-square';

                const imgElement = document.createElement('img');
                imgElement.className = 'image-thumbnail w-full h-full object-cover lazy-load';
                const imageUrl = `/view-file/${sessionId}/${encodeURIComponent(itemFullPath)}`;
                imgElement.src = lazyLoadPlaceholder;
                imgElement.dataset.src = imageUrl;
                imgElement.dataset.altOriginal = item.name;
                imgElement.alt = 'جاري تحميل الصورة...';

                imgElement.addEventListener('click', () => {
                    if (imgElement.classList.contains('loaded')) {
                        openImageModal(itemFullPath); // Use full path for modal
                    }
                });

                if (lazyImageObserver) {
                    lazyImageObserver.observe(imgElement);
                } else { // Fallback if observer not supported
                    imgElement.src = imageUrl;
                    imgElement.alt = item.name;
                    imgElement.classList.add('loaded');
                    imgElement.onerror = function() { this.src = errorImageSrc; this.alt = `فشل تحميل: ${item.name}`; };
                }

                const fileNamePara = document.createElement('p');
                fileNamePara.textContent = item.name;
                fileNamePara.className = 'filename-text-class';
                imgContainer.appendChild(imgElement);
                imgContainer.appendChild(fileNamePara);
                imageDisplayDiv.appendChild(imgContainer);
            }
        });

        fileListDiv.appendChild(fileListContainer);

        if (!imagesFoundOnPage && items.some(i => i.type === 'file') && imageDisplayDiv) {
            imageDisplayDiv.innerHTML = '<p class="text-gray-500 dark:text-gray-400 col-span-full p-4">لا توجد صور لعرضها في هذا المجلد.</p>';
        } else if (items.filter(i => i.type === 'file' && i.is_image).length === 0 && imageDisplayDiv) {
             imageDisplayDiv.innerHTML = '<p class="text-gray-500 dark:text-gray-400 col-span-full p-4">لا توجد صور لعرضها في هذا المجلد.</p>';
        }
    }


    function renderPaginationControlsForCurrentPath(currentPage, totalPages, totalItems, perPage) {
        if(!paginationControlsDiv || !fileListInfoDiv) return;
        paginationControlsDiv.innerHTML = '';
        fileListInfoDiv.innerHTML = '';

        if (!totalItems || totalItems === 0) { fileListInfoDiv.textContent = ''; return; }
        if (totalPages <= 1 && totalItems > 0) { fileListInfoDiv.textContent = `عرض ${totalItems} ${totalItems === 1 ? 'عنصر' : (totalItems === 2 ? 'عنصرين' : (totalItems >=3 && totalItems <=10 ? 'عناصر' : 'عنصرًا'))}.`; return; }


        const startItem = Math.min((currentPage - 1) * perPage + 1, totalItems);
        const endItem = Math.min(currentPage * perPage, totalItems);
        if (totalItems > 0) { fileListInfoDiv.textContent = `عرض العناصر ${startItem}-${endItem} من إجمالي ${totalItems}. (صفحة ${currentPage} من ${totalPages})`;}

        const createNavButton = (textOrHtml, targetPage, isDisabled, isNext = false) => {
            const button = document.createElement('button');
            button.innerHTML = textOrHtml;
            button.className = 'pagination-btn bg-sky-500 hover:bg-sky-600 dark:bg-sky-600 dark:hover:bg-sky-700 text-white font-semibold py-2 px-3 sm:px-4 rounded-md shadow text-sm sm:text-base transition-colors';
            if (isDisabled) {
                button.classList.add('disabled', 'opacity-50', 'cursor-not-allowed');
                button.disabled = true;
            } else {
                button.addEventListener('click', () => {
                    currentPageGlobal = targetPage;
                    displayCurrentPathItemsPage();
                });
            }
            return button;
        };

        paginationControlsDiv.appendChild(createNavButton('<i class="fas fa-chevron-right ml-1"></i> السابق', currentPage - 1, currentPage === 1, false));

        const pageInfoSpan = document.createElement('span');
        pageInfoSpan.className = 'px-2 py-1 sm:px-3 sm:py-2 rounded-md text-xs sm:text-sm font-medium bg-gray-200 dark:bg-gray-700';
        pageInfoSpan.textContent = `صفحة ${currentPage} / ${totalPages}`;
        paginationControlsDiv.appendChild(pageInfoSpan);

        paginationControlsDiv.appendChild(createNavButton('التالي <i class="fas fa-chevron-left mr-1"></i>', currentPage + 1, currentPage === totalPages, true));
    }

    function updateImageListInfoForCurrentPath(currentPathPageItems, totalItemsInCurrentPath) {
        if (!imageListInfoDiv) return;

        const totalImagesInCurrentPath = currentDisplayedItems.filter(item => item.type === 'file' && item.is_image).length;
        const imagesInCurrentPage = currentPathPageItems.filter(item => item.type === 'file' && item.is_image);

        if (totalImagesInCurrentPath === 0) {
            imageListInfoDiv.textContent = 'لا توجد صور في هذا المجلد.';
        } else if (imagesInCurrentPage.length > 0) {
            const imageText = imagesInCurrentPage.length === 1 ? 'صورة واحدة' : (imagesInCurrentPage.length === 2 ? 'صورتين' : `${imagesInCurrentPage.length} صور`);
            const totalImageText = totalImagesInCurrentPath === 1 ? 'صورة واحدة' : (totalImagesInCurrentPath === 2 ? 'صورتين' : `${totalImagesInCurrentPath} صور`);
            imageListInfoDiv.textContent = `عرض ${imageText} في هذه الصفحة (من إجمالي ${totalImageText} في المجلد الحالي).`;
        } else {
             const totalImageText = totalImagesInCurrentPath === 1 ? 'صورة واحدة' : (totalImagesInCurrentPath === 2 ? 'صورتين' : `${totalImagesInCurrentPath} صور`);
             imageListInfoDiv.textContent = `لا توجد صور لعرضها في هذه الصفحة (إجمالي ${totalImageText} في المجلد الحالي).`;
        }
    }


    // --- Modal Functions ---
    function displayImageInModal(index) {
        if (!allImagesForModalNavigation || allImagesForModalNavigation.length === 0 || index < 0 || index >= allImagesForModalNavigation.length) {
            closeImageModal(); return;
        }
        currentModalImageIndex = index;
        const imageData = allImagesForModalNavigation[index];
        if(!modalImage || !modalImageName || !modalImageCounter || !modalSpinner) return;

        modalImage.classList.remove('opacity-100', 'loaded');
        modalImage.classList.add('opacity-0');
        modalSpinner.classList.remove('hidden');
        modalSpinner.classList.add('flex');
        modalImage.src = lazyLoadPlaceholder; // Placeholder while loading
        modalImage.alt = "جاري تحميل " + imageData.name + "...";
        modalImageName.textContent = imageData.name;
        modalImageCounter.textContent = `${index + 1} / ${allImagesForModalNavigation.length}`;

        const tempImg = new Image();
        tempImg.onload = () => {
            modalImage.src = imageData.downloadUrl;
            modalImage.alt = imageData.name;
            setTimeout(() => { // Slight delay for smoother transition
                if(modalSpinner) { modalSpinner.classList.add('hidden'); modalSpinner.classList.remove('flex'); }
                modalImage.classList.remove('opacity-0');
                modalImage.classList.add('opacity-100', 'loaded');
            }, 50);
        };
        tempImg.onerror = () => {
            modalImage.src = errorImageSrc;
            modalImage.alt = `فشل تحميل: ${imageData.name}`;
            if(modalSpinner) { modalSpinner.classList.add('hidden'); modalSpinner.classList.remove('flex'); }
            modalImage.classList.remove('opacity-0');
            modalImage.classList.add('opacity-100', 'loaded');
        };
        tempImg.src = imageData.downloadUrl; // Start loading

        if(modalPrevButton) { modalPrevButton.disabled = (index === 0); modalPrevButton.classList.toggle('disabled', index === 0); }
        if(modalNextButton) { modalNextButton.disabled = (index === allImagesForModalNavigation.length - 1); modalNextButton.classList.toggle('disabled', index === allImagesForModalNavigation.length - 1); }
    }

    function openImageModal(clickedImageFullPath) {
        if (!currentSessionId || !allImagesForModalNavigation || allImagesForModalNavigation.length === 0) return;
        const initialIndex = allImagesForModalNavigation.findIndex(img => img.path === clickedImageFullPath);

        if (initialIndex !== -1 && imageModal) {
            imageModal.classList.remove('hidden');
            setTimeout(() => { imageModal.classList.add('flex'); imageModal.classList.remove('opacity-0'); }, 10); // For transition
            document.body.classList.add('modal-open');
            displayImageInModal(initialIndex);
        }
    }

    function closeImageModal() {
        if (imageModal) {
            imageModal.classList.add('opacity-0');
            setTimeout(() => {
                imageModal.classList.add('hidden');
                imageModal.classList.remove('flex');
                if (modalImage) { modalImage.src = lazyLoadPlaceholder; modalImage.alt = ""; modalImage.classList.remove('opacity-100','loaded'); modalImage.classList.add('opacity-0');}
                if (modalImageName) modalImageName.textContent = "";
                if (modalImageCounter) modalImageCounter.textContent = "";
                if(modalSpinner) {modalSpinner.classList.add('hidden'); modalSpinner.classList.remove('flex');}
            }, 300); // Match transition duration
            document.body.classList.remove('modal-open');
        }
    }

    // Event Listeners for Modal
    if (modalCloseButton) modalCloseButton.addEventListener('click', closeImageModal);
    if (imageModal) imageModal.addEventListener('click', (event) => { if (event.target === imageModal) closeImageModal(); }); // Close on backdrop click
    document.addEventListener('keydown', (event) => {
        if (!imageModal || imageModal.classList.contains('hidden')) return;
        if (event.key === 'Escape') closeImageModal();
        if (event.key === 'ArrowLeft' && modalPrevButton && !modalPrevButton.disabled) modalPrevButton.click(); // Left arrow for "previous" (button on right)
        if (event.key === 'ArrowRight' && modalNextButton && !modalNextButton.disabled) modalNextButton.click(); // Right arrow for "next" (button on left)
    });
    if(modalPrevButton) modalPrevButton.addEventListener('click', (e) => { e.stopPropagation(); if (currentModalImageIndex > 0) displayImageInModal(currentModalImageIndex - 1); });
    if(modalNextButton) modalNextButton.addEventListener('click', (e) => { e.stopPropagation(); if (currentModalImageIndex < allImagesForModalNavigation.length - 1) displayImageInModal(currentModalImageIndex + 1); });


    // --- Tab Functions ---
    function switchTab(targetTabId) {
        if (!tabContentAllFiles || !tabContentImagesOnly || !tabAllFilesButton || !tabImagesOnlyButton) return;
        document.querySelectorAll('.tab-content-panel').forEach(panel => panel.classList.add('hidden'));
        document.querySelectorAll('.tab-button').forEach(button => {
            button.classList.remove('active-tab'); // Simplified class removal, Tailwind handles the rest
        });
        const targetPanel = document.getElementById(`tab-content-${targetTabId}`);
        if (targetPanel) targetPanel.classList.remove('hidden');
        const targetButton = document.getElementById(`tab-${targetTabId}`);
        if (targetButton) {
            targetButton.classList.add('active-tab');
        }

        if (targetTabId === 'images-only') {
            const { files, folders } = getItemsForPath(currentPathInTree, fullArchiveStructure);
            const pageItemsForInfo = [...folders, ...files].slice( (currentPageGlobal - 1) * filesPerPageGlobal, currentPageGlobal * filesPerPageGlobal);
            updateImageListInfoForCurrentPath(pageItemsForInfo, currentDisplayedItems.length);
        }
    }
    if (tabAllFilesButton) tabAllFilesButton.addEventListener('click', () => switchTab('all-files'));
    if (tabImagesOnlyButton) tabImagesOnlyButton.addEventListener('click', () => switchTab('images-only'));


    // --- Intersection Observer for Lazy Loading Images ---
    let lazyImageObserver;
    const observerOptions = { root: null, rootMargin: '0px 0px 200px 0px', threshold: 0.01 };

    function handleImageIntersection(entries, observer) {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const lazyImage = entry.target;
                const actualImageLoader = new Image();
                actualImageLoader.onload = () => {
                    lazyImage.src = lazyImage.dataset.src;
                    lazyImage.alt = lazyImage.dataset.altOriginal || 'صورة محملة';
                    lazyImage.classList.remove('lazy-load');
                    lazyImage.classList.add('loaded');
                };
                actualImageLoader.onerror = () => {
                    lazyImage.src = errorImageSrc;
                    lazyImage.alt = `فشل تحميل: ${lazyImage.dataset.altOriginal || 'صورة'}`;
                    lazyImage.classList.remove('lazy-load');
                    lazyImage.classList.add('error');
                    if(lazyImage.parentElement) lazyImage.parentElement.classList.add('flex', 'items-center', 'justify-center', 'bg-gray-100', 'dark:bg-gray-800');
                };
                actualImageLoader.src = lazyImage.dataset.src;
                observer.unobserve(lazyImage);
            }
        });
    }

    if ("IntersectionObserver" in window) {
        lazyImageObserver = new IntersectionObserver(handleImageIntersection, observerOptions);
    } else {
        console.warn("IntersectionObserver not supported. Lazy loading disabled.");
        document.querySelectorAll('img.lazy-load').forEach(img => {
            img.src = img.dataset.src;
            img.alt = img.dataset.altOriginal || 'صورة';
            img.classList.remove('lazy-load');
            img.classList.add('loaded');
        });
    }

    if (tabAllFilesButton) {
        switchTab('all-files');
    }
    navigateToPath('');
});
