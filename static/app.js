// Tab switching
function showTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    
    event.target.classList.add('active');
    document.getElementById(`${tabName}-tab`).classList.add('active');
    
    if (tabName === 'process') {
        refreshFiles();
    } else if (tabName === 'downloaded') {
        refreshDownloaded();
    } else if (tabName === 'settings') {
        // Load channels when Settings tab is opened to show the correct saved value
        if (!channelsLoaded) {
            loadChannels();
        }
    }
}

// Progress logging with polling
let progressPollInterval = null;
let currentJobId = null;

function startProgressPolling(jobId) {
    currentJobId = jobId;
    stopProgressPolling();
    
    let logsLoaded = false;
    
    progressPollInterval = setInterval(() => {
        fetch(`/progress/${jobId}`)
        .then(r => r.json())
        .then(data => {
            // Load historical logs on first poll (for page refresh)
            if (!logsLoaded && data.logs && data.logs.length > 0) {
                logsLoaded = true;
                data.logs.forEach(log => {
                    logProgress(log.msg, log.type);
                });
            }
            
            // Don't log "COMPLETE" messages - they're just status markers
            if (data.message && data.message !== 'COMPLETE') {
                logProgress(data.message);
            }
            if (data.complete) {
                console.log('[POLLING] Job complete, results:', data.results);
                stopProgressPolling();
                if (data.results && data.results.length > 0) {
                    console.log('[POLLING] Setting window.compressionResults:', data.results);
                    window.compressionResults = data.results;
                }
            }
        })
        .catch(err => {
            // Silent fail - polling will retry
        });
    }, 500);
}

function stopProgressPolling() {
    if (progressPollInterval) {
        clearInterval(progressPollInterval);
        progressPollInterval = null;
    }
}

// Load files
let allFiles = [];
let showAllFiles = false;

function refreshFiles() {
    fetch('/files')
    .then(r => r.json())
    .then(files => {
        allFiles = files;
        displayFiles();
    });
}

function displayFiles() {
    const processList = document.getElementById('processFileList');
    const toggleBtn = document.getElementById('toggleFilesBtn');
    
    if (allFiles.length === 0) {
        processList.innerHTML = '<p style="color:#0f0;">No files in archive folder</p>';
        toggleBtn.style.display = 'none';
        return;
    }
    
    // Show first 10 or all files based on toggle
    const filesToShow = showAllFiles ? allFiles : allFiles.slice(0, 10);
    
    processList.innerHTML = filesToShow.map(f => `
        <div class="file-item">
            <input type="checkbox" class="process-checkbox" data-file="${f.name}" onchange="updateSelectedCount()">
            <div class="file-info">
                <div class="file-name">${f.name}</div>
                <div class="file-size">${f.size_gb} GB • ${f.modified}</div>
            </div>
            <button class="btn btn-danger" data-file-path="${f.name}">[ Delete ]</button>
        </div>
    `).join('');
    
    // Add event listeners to delete buttons
    processList.querySelectorAll('.btn-danger').forEach(btn => {
        btn.addEventListener('click', function() {
            const filePath = this.getAttribute('data-file-path');
            deleteFile(filePath);
        });
    });
    
    // Update toggle button
    if (allFiles.length > 10) {
        toggleBtn.style.display = 'inline-block';
        toggleBtn.textContent = showAllFiles ? `[ Hide All ]` : `[ Show All (${allFiles.length - 10} more) ]`;
    } else {
        toggleBtn.style.display = 'none';
    }
    
    updateSelectedCount();
}

function toggleFileList() {
    showAllFiles = !showAllFiles;
    
    // Reset "Select All" checkbox when toggling view
    const selectAllCheckbox = document.getElementById('selectAllFiles');
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
    }
    
    displayFiles();
}

function toggleSelectAll() {
    const selectAllCheckbox = document.getElementById('selectAllFiles');
    const checkboxes = document.querySelectorAll('.process-checkbox');
    
    checkboxes.forEach(cb => {
        cb.checked = selectAllCheckbox.checked;
    });
    
    updateSelectedCount();
}

function updateSelectedCount() {
    const selectedCheckboxes = document.querySelectorAll('.process-checkbox:checked');
    const count = selectedCheckboxes.length;
    const countEl = document.getElementById('selectedCount');
    
    if (countEl) {
        countEl.textContent = `${count} selected`;
        countEl.style.color = count > 0 ? '#0f0' : '#888';
    }
}

// Process selected files
let bypassRawWarning = false;

function processSelectedFiles() {
    const selectedFiles = Array.from(document.querySelectorAll('.process-checkbox:checked'))
        .map(cb => cb.dataset.file);
    
    if (selectedFiles.length === 0) {
        showStatus('No files selected', 'error');
        return;
    }
    
    const compress = document.getElementById('processCompress').checked;
    const bundle = document.getElementById('processBundleArchive').checked;
    const encrypt = document.getElementById('processEncrypt').checked;
    const upload = document.getElementById('processUpload').checked;
    let uploadDest = document.getElementById('uploadDestination').value;
    
    // If upload destination is the special "__load__" value or empty, fetch from server config
    if (uploadDest === '__load__' || uploadDest === '') {
        // Fetch the actual config value from server
        fetch('/settings')
        .then(r => r.json())
        .then(config => {
            uploadDest = config.upload_destination || 'me';
            continueWithUploadDest();
        })
        .catch(err => {
            uploadDest = 'me'; // Fallback
            continueWithUploadDest();
        });
        return; // Exit and wait for config fetch
    }
    
    continueWithUploadDest();
    
    function continueWithUploadDest() {
    
    // Check if credentials are set when trying to upload
    if (upload) {
        const hasApiId = document.getElementById('telegram_api_id').value.trim() !== '';
        const hasApiHash = document.getElementById('telegram_api_hash').value.trim() !== '';
        
        if (!hasApiId || !hasApiHash) {
            clearProgress();
            logProgress('[ERROR] Cannot upload: Telegram API credentials not set', 'error');
            logProgress('[INFO] Please go to Settings tab and enter your API ID and Hash from https://my.telegram.org/auth', 'info');
            showStatus('Error: Telegram credentials required for upload', 'error');
            return;
        }
        
        // Verify upload configuration with backend
        fetch('/verify-upload-config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({destination: uploadDest})
        }).then(r => {
            if (!r.ok) {
                return r.json().then(data => {
                    throw new Error(data.error || 'Upload configuration invalid');
                });
            }
            return r.json();
        }).then(verifyData => {
            if (!verifyData.valid) {
                throw new Error(verifyData.error || 'Upload configuration invalid');
            }
            logProgress(`[VERIFY] Upload destination verified: ${verifyData.destination}`, 'success');
            // Continue with the actual processing
            continueProcessing();
        }).catch(err => {
            clearProgress();
            logProgress(`[ERROR] Upload verification failed: ${err.message}`, 'error');
            showStatus('Error: ' + err.message, 'error');
        });
        return; // Exit here, will continue in continueProcessing()
    }
    
    // If not uploading, continue directly
    continueProcessing();
    
    function continueProcessing() {
    
    // Check if password is set when trying to encrypt
    if (encrypt) {
        const hasPassword = document.getElementById('password').value.trim() !== '';
        
        if (!hasPassword) {
            clearProgress();
            logProgress('[ERROR] Cannot encrypt: Password not set', 'error');
            logProgress('[INFO] Please go to Settings tab and set an encryption password', 'info');
            showStatus('Error: Password required for encryption', 'error');
            return;
        }
    }
    
    // Check if files are already encrypted (.7z files)
    const alreadyEncrypted = selectedFiles.some(f => f.endsWith('.7z') || f.match(/\.7z\.\d+$/));
    
    // If files are already encrypted, skip to upload
    if (alreadyEncrypted && upload && !encrypt) {
        clearProgress();
        logProgress(`[START] Uploading ${selectedFiles.length} encrypted file(s)...`);
        
        // Upload only the selected files, not the entire folder
        logProgress(`[UPLOAD] Starting upload of selected file(s)...`);
        fetch('/telegram-upload-files', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({files: selectedFiles, destination: uploadDest})
        }).then(r => r.json()).then(uploadData => {
            if (uploadData.job_id) {
                startProgressPolling(uploadData.job_id);
                // Wait for upload to complete
                const checkComplete = setInterval(() => {
                    fetch(`/progress/${uploadData.job_id}`)
                    .then(r => r.json())
                    .then(progressData => {
                        if (progressData.complete) {
                            clearInterval(checkComplete);
                            logProgress(`[OK] Uploaded ${selectedFiles.length} file(s) to Telegram`, 'success');
                            logProgress(`[COMPLETE] Upload finished successfully!`, 'success');
                            showStatus('Upload complete!', 'success');
                            refreshFiles();
                        }
                    });
                }, 500);
            } else if (uploadData.error) {
                throw new Error(uploadData.error);
            }
        }).catch(err => {
            showLoading(false);
            logProgress(`[ERROR] ${err.message}`, 'error');
            showStatus('Error: ' + err.message, 'error');
        });
        return;
    }
    
    // Validation: If not encrypting, warn user about privacy
    if (upload && !encrypt && !alreadyEncrypted && !bypassRawWarning) {
        // Check if user has disabled the warning
        const dontShowWarning = localStorage.getItem('dontShowRawWarning') === 'true';
        
        if (!dontShowWarning) {
            // Show modal and wait for user response
            showRawUploadWarning();
            return; // Stop here, will be called again after confirmation
        }
    }
    
    // Reset bypass flag
    bypassRawWarning = false;
    
    clearProgress();
    logProgress(`[START] Processing ${selectedFiles.length} file(s)...`);
    logProgress(`[OPTIONS] Compress: ${compress ? 'Yes' : 'No'} | Bundle: ${bundle ? 'Yes' : 'No'} | Encrypt: ${encrypt ? 'Yes' : 'No'} | Upload: ${upload ? 'Yes' : 'No'}`);
    
    let processedFiles = selectedFiles;
    
    // Step 1: Compress videos if enabled
    const videoFiles = selectedFiles.filter(f => /\.(mp4|avi|mkv|mov|flv|wmv|webm)$/i.test(f));
    const nonVideoFiles = selectedFiles.filter(f => !/\.(mp4|avi|mkv|mov|flv|wmv|webm)$/i.test(f));
    
    logProgress(`[DETECT] Found ${videoFiles.length} video(s) and ${nonVideoFiles.length} other file(s)`);
    
    let compressPromise = Promise.resolve({success: true, results: []});
    
    if (compress && videoFiles.length > 0) {
        logProgress(`[COMPRESS] Starting compression of ${videoFiles.length} video(s)...`);
        compressPromise = fetch('/compress', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                files: videoFiles, 
                keep_audio: true,
                initial_logs: [
                    {msg: `[START] Processing ${selectedFiles.length} file(s)...`, type: 'info'},
                    {msg: `[OPTIONS] Compress: ${compress ? 'Yes' : 'No'} | Encrypt: ${encrypt ? 'Yes' : 'No'} | Upload: ${upload ? 'Yes' : 'No'}`, type: 'info'},
                    {msg: `[DETECT] Found ${videoFiles.length} video(s) and ${nonVideoFiles.length} other file(s)`, type: 'info'},
                    {msg: `[COMPRESS] Starting compression of ${videoFiles.length} video(s)...`, type: 'info'}
                ]
            })
        }).then(r => r.json()).then(data => {
            if (data.job_id) {
                startProgressPolling(data.job_id);
            }
            return data;
        });
    } else if (videoFiles.length > 0) {
        logProgress(`[SKIP] Compression disabled, will process videos as-is`);
    }
    
    compressPromise
    .then(data => {
        if (data.success && data.job_id) {
            // Wait for compression to complete via polling
            return new Promise((resolve, reject) => {
                let attempts = 0;
                const maxAttempts = 7200; // 60 minutes (7200 * 500ms) - compression can take a long time
                
                const checkComplete = setInterval(() => {
                    attempts++;
                    if (attempts % 20 === 0) { // Log every 10 seconds
                        console.log(`[COMPRESS WAIT] Attempt ${attempts}, checking window.compressionResults:`, window.compressionResults);
                    }
                    
                    if (window.compressionResults) {
                        console.log('[COMPRESS WAIT] Found results, continuing...');
                        clearInterval(checkComplete);
                        resolve({success: true, results: window.compressionResults});
                        window.compressionResults = null;
                    } else if (attempts >= maxAttempts) {
                        clearInterval(checkComplete);
                        reject(new Error('Compression timeout - results not received after 60 minutes'));
                    }
                }, 500);
            });
        } else if (compress && videoFiles.length > 0) {
            throw new Error(data.error || 'Compression failed');
        }
        return {success: true, results: []};
    })
    .then(data => {
        if (data.results && data.results.length > 0) {
            logProgress(`[OK] Compressed ${data.results.length} video(s)`, 'success');
            data.results.forEach(r => {
                logProgress(`    → ${r.original} → ${r.compressed} (${r.size} GB)`);
            });
            processedFiles = [...data.results.map(r => r.compressed), ...nonVideoFiles];
        }
        
        // Step 2: Archive/Encrypt if enabled
        if (bundle || encrypt) {
            const action = encrypt ? 'encryption' : 'archiving';
            logProgress(`[ARCHIVE] Starting ${action} of ${processedFiles.length} file(s)...`);
            
            // Enable auto-upload for separate files if upload is enabled
            const autoUpload = upload && !bundle;
            
            return fetch('/encrypt', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    files: processedFiles, 
                    bundle: bundle, 
                    encrypt: encrypt,
                    auto_upload: autoUpload,
                    upload_destination: uploadDest
                })
            }).then(r => r.json()).then(encryptData => {
                if (encryptData.job_id) {
                    // Start polling for encryption progress
                    startProgressPolling(encryptData.job_id);
                    // Wait for encryption to complete
                    return new Promise((resolve) => {
                        const checkComplete = setInterval(() => {
                            fetch(`/progress/${encryptData.job_id}`)
                            .then(r => r.json())
                            .then(progressData => {
                                if (progressData.complete) {
                                    clearInterval(checkComplete);
                                    resolve(progressData.result);
                                }
                            });
                        }, 500);
                    });
                }
                return encryptData;
            });
        }
        return {success: true, folder: null};
    })
    .then(data => {
        showLoading(false);
        
        // Step 3: Upload if enabled (skip if already uploaded via auto_upload)
        if (upload) {
            // Check if files were already uploaded during encryption (separate file processing with auto_upload)
            if (data.uploaded !== undefined && data.uploaded > 0) {
                logProgress(`[COMPLETE] Processing finished successfully!`, 'success');
                showStatus('Processing complete!', 'success');
                refreshFiles();
                return {success: true, uploaded: data.uploaded};
            }
            
            // Check if we need to upload raw files or archived files
            if (!bundle && !encrypt) {
                // Upload raw files directly
                logProgress(`[UPLOAD] Starting raw file upload to Telegram...`);
                return fetch('/telegram-upload-raw', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({files: processedFiles, destination: uploadDest})
                }).then(r => r.json()).then(uploadData => {
                    if (uploadData.job_id) {
                        startProgressPolling(uploadData.job_id);
                        return new Promise((resolve) => {
                            const checkComplete = setInterval(() => {
                                fetch(`/progress/${uploadData.job_id}`)
                                .then(r => r.json())
                                .then(progressData => {
                                    if (progressData.complete) {
                                        clearInterval(checkComplete);
                                        resolve({success: true, uploaded: uploadData.files});
                                    }
                                });
                            }, 500);
                        });
                    }
                    return uploadData;
                });
            } else if (data.success && data.folder) {
                // Upload archived files
                if (data.split) {
                    logProgress(`[OK] Encrypted and split into ${data.parts} part(s): ${data.folder}`, 'success');
                } else {
                    logProgress(`[OK] Encrypted to folder: ${data.folder}`, 'success');
                }
                
                logProgress(`[UPLOAD] Starting upload to Telegram...`);
                return fetch('/telegram-upload', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({folder: data.folder, destination: uploadDest})
                }).then(r => r.json()).then(uploadData => {
                    if (uploadData.job_id) {
                        startProgressPolling(uploadData.job_id);
                        return new Promise((resolve) => {
                            const checkComplete = setInterval(() => {
                                fetch(`/progress/${uploadData.job_id}`)
                                .then(r => r.json())
                                .then(progressData => {
                                    if (progressData.complete) {
                                        clearInterval(checkComplete);
                                        resolve({success: true, uploaded: uploadData.parts});
                                    }
                                });
                            }, 500);
                        });
                    }
                    return uploadData;
                });
            }
        }
        return data;
    })
    .then(data => {
        showLoading(false);
        if (data.success) {
            if (data.uploaded) {
                logProgress(`[OK] Uploaded ${data.uploaded} file(s) to Telegram`, 'success');
            }
            logProgress(`[COMPLETE] Processing finished successfully!`, 'success');
            showStatus('Processing complete!', 'success');
            refreshFiles();
        } else {
            throw new Error(data.error || 'Processing failed');
        }
    })
    .catch(err => {
        showLoading(false);
        stopProgressPolling();
        logProgress(`[ERROR] ${err.message}`, 'error');
        showStatus('Error: ' + err.message, 'error');
    });
    } // End of continueProcessing function
    } // End of continueWithUploadDest function
}

// Load Telegram archives
let currentArchives = [];

function loadTelegramArchives() {
    // Check if credentials are set
    const hasApiId = document.getElementById('telegram_api_id').value.trim() !== '';
    const hasApiHash = document.getElementById('telegram_api_hash').value.trim() !== '';
    
    if (!hasApiId || !hasApiHash) {
        clearProgress();
        logProgress('[ERROR] Cannot fetch archives: Telegram API credentials not set', 'error');
        logProgress('[INFO] Please go to Settings tab and enter your API ID and Hash from https://my.telegram.org/auth', 'info');
        showStatus('Error: Telegram credentials required', 'error');
        return;
    }
    
    showLoading(true);
    clearProgress();
    logProgress('Fetching files from Telegram...');
    
    fetch('/telegram-archives')
    .then(r => r.json())
    .then(data => {
        showLoading(false);
        if (data.success) {
            logProgress(`[OK] Found ${data.archives.length} archive(s)`, 'success');
            currentArchives = data.archives;
            displayTelegramArchives(currentArchives);
        } else {
            logProgress(`[ERROR] ${data.error}`, 'error');
            showStatus(data.error || 'Failed to fetch archives', 'error');
        }
    })
    .catch(err => {
        showLoading(false);
        logProgress(`[ERROR] ${err.message}`, 'error');
        showStatus('Error: ' + err.message, 'error');
    });
}

function displayTelegramArchives(archives) {
    const list = document.getElementById('telegramArchivesList');
    
    if (archives.length === 0) {
        list.innerHTML = '<p style="color:#0f0;">No archives found in Telegram</p>';
        return;
    }
    
    list.innerHTML = archives.map(archive => `
        <div class="folder-item">
            <div class="folder-info">
                <h3>[ ${archive.name} ]</h3>
                <p>${archive.parts} part(s) • ${archive.total_size} • Uploaded: ${archive.date}</p>
                ${archive.expanded ? `
                    <div style="margin-top: 10px; padding-left: 20px; color: #888; font-size: 13px;">
                        ${archive.files.map(f => `
                            <div style="display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid #222;">
                                <span>→ ${f.name} (${f.size})</span>
                                <button class="btn" style="padding: 6px 12px; font-size: 12px; margin: 0;" onclick="downloadSingleFile('${archive.id}', '${f.name}', ${f.message_id})">[ Download ]</button>
                            </div>
                        `).join('')}
                    </div>
                ` : ''}
            </div>
            <div class="folder-actions">
                <button class="btn" onclick="toggleArchiveParts('${archive.id}')">[ ${archive.expanded ? 'Hide' : 'Show'} Parts ]</button>
                <button class="btn" onclick="downloadArchive('${archive.id}')">[ Download All ]</button>
                <button class="btn btn-danger" onclick="deleteTelegramArchive('${archive.id}')">[ Delete ]</button>
            </div>
        </div>
    `).join('');
}

function toggleArchiveParts(archiveId) {
    const archive = currentArchives.find(a => a.id === archiveId);
    if (archive) {
        archive.expanded = !archive.expanded;
        displayTelegramArchives(currentArchives);
    }
}

function downloadSingleFile(archiveId, filename, messageId) {
    clearProgress();
    logProgress(`[DOWNLOAD] Starting download of: ${filename}...`);
    
    fetch('/telegram-download-single', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            archive_id: archiveId,
            filename: filename,
            message_id: messageId
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.job_id) {
            startProgressPolling(data.job_id);
            // Wait for download to complete
            const checkComplete = setInterval(() => {
                fetch(`/progress/${data.job_id}`)
                .then(r => r.json())
                .then(progressData => {
                    if (progressData.complete) {
                        clearInterval(checkComplete);
                        if (progressData.result && progressData.result.path) {
                            logProgress(`[OK] Downloaded to: ${progressData.result.path}`, 'success');
                            showStatus('Download complete!', 'success');
                        }
                    }
                });
            }, 500);
        } else if (data.error) {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        logProgress(`[ERROR] ${err.message}`, 'error');
        showStatus('Error: ' + err.message, 'error');
    });
}

function downloadArchive(archiveId) {
    const globalDecrypt = document.getElementById('globalDecrypt').checked;
    const deleteAfterDecrypt = document.getElementById('globalDeleteAfterDecrypt').checked;
    
    clearProgress();
    logProgress(`[DOWNLOAD] Starting download of archive: ${archiveId}...`);
    if (globalDecrypt) {
        logProgress(`[DOWNLOAD] Will decrypt after download`, 'info');
        if (deleteAfterDecrypt) {
            logProgress(`[DOWNLOAD] Will delete .7z files after decryption`, 'info');
        }
    }
    
    fetch('/telegram-download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            archive_id: archiveId, 
            decrypt: globalDecrypt,
            delete_after_decrypt: deleteAfterDecrypt
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.job_id) {
            startProgressPolling(data.job_id);
            // Wait for download to complete
            const checkComplete = setInterval(() => {
                fetch(`/progress/${data.job_id}`)
                .then(r => r.json())
                .then(progressData => {
                    if (progressData.complete) {
                        clearInterval(checkComplete);
                        if (progressData.result && progressData.result.path) {
                            logProgress(`[OK] Downloaded to: ${progressData.result.path}`, 'success');
                            showStatus('Download complete!', 'success');
                        }
                    }
                });
            }, 500);
        } else if (data.error) {
            throw new Error(data.error);
        }
    })
    .catch(err => {
        logProgress(`[ERROR] ${err.message}`, 'error');
        showStatus('Error: ' + err.message, 'error');
    });
}

function deleteTelegramArchive(archiveId) {
    if (!confirm('Delete this archive from Telegram?')) return;
    
    showLoading(true);
    clearProgress();
    logProgress(`Deleting archive: ${archiveId}...`);
    
    fetch('/telegram-delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({archive_id: archiveId})
    })
    .then(r => r.json())
    .then(data => {
        showLoading(false);
        if (data.success) {
            logProgress(`[OK] Deleted ${data.deleted} file(s)`, 'success');
            showStatus('Archive deleted', 'success');
            loadTelegramArchives();
        } else {
            logProgress(`[ERROR] ${data.error}`, 'error');
            showStatus(data.error || 'Delete failed', 'error');
        }
    })
    .catch(err => {
        showLoading(false);
        logProgress(`[ERROR] ${err.message}`, 'error');
        showStatus('Error: ' + err.message, 'error');
    });
}

// Progress logging
let completedProgressBars = new Set(); // Track which progress bars have completed

function logProgress(message, type = 'info') {
    const log = document.getElementById('progressLog');
    log.classList.add('active');
    
    // Check if message contains percentage for progress bar
    const percentMatch = message.match(/(\d+\.?\d*)%/);
    
    if (percentMatch && (message.includes('Compressing') || message.includes('Encrypting') || message.includes('Uploading') || message.includes('Downloading') || message.includes('complete'))) {
        const percent = parseFloat(percentMatch[1]);
        const isCompressing = message.includes('Compressing');
        const isEncrypting = message.includes('Encrypting');
        const isUploading = message.includes('Uploading');
        const isDownloading = message.includes('Downloading');
        
        let containerId, barId, textId, label, speedInfo = '';
        if (isCompressing) {
            containerId = 'compression-progress-container';
            barId = 'compression-progress-bar';
            textId = 'compression-progress-text';
            label = 'Compressing video...';
        } else if (isEncrypting) {
            containerId = 'encryption-progress-container';
            barId = 'encryption-progress-bar';
            textId = 'encryption-progress-text';
            label = 'Encrypting files...';
        } else if (isUploading) {
            containerId = 'upload-progress-container';
            barId = 'upload-progress-bar';
            textId = 'upload-progress-text';
            // Extract file info and speed from message like "Uploading [1/2]: 5.1% (2.34 MB/s) - file.7z.001"
            const fileMatch = message.match(/\[(\d+)\/(\d+)\].*?-\s*(.+)/);
            const speedMatch = message.match(/\(([0-9.]+\s*MB\/s)\)/);
            if (fileMatch) {
                label = `Uploading file ${fileMatch[1]}/${fileMatch[2]}: ${fileMatch[3]}`;
            } else {
                label = 'Uploading to Telegram...';
            }
            if (speedMatch) {
                speedInfo = ` • ${speedMatch[1]}`;
            }
        } else if (isDownloading) {
            containerId = 'download-progress-container';
            barId = 'download-progress-bar';
            textId = 'download-progress-text';
            // Extract file info from message like "Downloading [1/2]: 5.1% - file.7z.001"
            const fileMatch = message.match(/\[(\d+)\/(\d+)\].*?-\s*(.+)/);
            if (fileMatch) {
                label = `Downloading file ${fileMatch[1]}/${fileMatch[2]}: ${fileMatch[3]}`;
            } else {
                label = 'Downloading from Telegram...';
            }
        }
        
        // Check if progress bar already exists
        let progressBar = document.getElementById(barId);
        if (!progressBar) {
            const container = document.createElement('div');
            container.id = containerId;
            container.style.marginTop = '15px';
            container.innerHTML = `
                <div style="color: #0f0; margin-bottom: 10px; font-size: 14px;" id="${containerId}-label">[ ${label} ]</div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill" id="${barId}" style="width: 0%"></div>
                    <div class="progress-bar-text" id="${textId}">0%</div>
                </div>
            `;
            log.appendChild(container);
            progressBar = document.getElementById(barId);
        } else {
            // Update label for uploads (file name changes)
            if (isUploading) {
                const labelEl = document.getElementById(`${containerId}-label`);
                if (labelEl) {
                    labelEl.textContent = `[ ${label} ]`;
                }
            }
        }
        
        // Update progress bar
        if (progressBar) {
            progressBar.style.width = percent + '%';
            const textEl = document.getElementById(textId);
            if (textEl) {
                textEl.textContent = percent.toFixed(1) + '%' + speedInfo;
            }
        }
        
        // When complete (100%), remove progress bar after showing completion (only once)
        if (percent >= 100 && !completedProgressBars.has(containerId)) {
            completedProgressBars.add(containerId);
            setTimeout(() => {
                const container = document.getElementById(containerId);
                if (container) {
                    container.remove();
                }
                // Add completion message
                const div = document.createElement('div');
                div.className = 'success';
                const completionLabel = isCompressing ? 'Compression' : isEncrypting ? 'Encryption' : isUploading ? 'Upload' : 'Download';
                div.textContent = `[${new Date().toLocaleTimeString()}] ${completionLabel} complete!`;
                log.appendChild(div);
                log.scrollTop = log.scrollHeight;
                
                // Remove from completed set after cleanup
                setTimeout(() => completedProgressBars.delete(containerId), 1000);
            }, 800);
        }
    } else {
        // Regular log message (but skip duplicates and completion messages)
        const skipMessages = [
            'Uploading [',
            'Downloading [',
            '100% complete',  // Skip backend completion messages - progress bar handles it
            'Encrypting: 100%',
            'Archiving: 100%',
            'Compressing: 100%'
        ];
        
        const shouldSkip = skipMessages.some(skip => message.includes(skip)) && message.includes('%');
        
        if (!shouldSkip) {
            const div = document.createElement('div');
            div.className = type;
            div.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            log.appendChild(div);
        }
    }
    
    log.scrollTop = log.scrollHeight;
}

function clearProgress() {
    const log = document.getElementById('progressLog');
    const logsPlaceholder = document.getElementById('logsPlaceholder');
    const isAtTop = logsPlaceholder && logsPlaceholder.contains(log);
    const btnText = isAtTop ? '[ ↓ Move to Bottom ]' : '[ ↑ Move to Top ]';
    
    log.innerHTML = '<div style="display: flex; justify-content: space-between; align-items: center; color: #0f0; font-size: 16px; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #333; font-family: \'SFMono-Regular\', \'SF Mono\', \'Inconsolata\', \'Terminus\', monospace;"><span>[ Console Logs ]</span><button class="btn" onclick="toggleLogsPosition()" id="toggleLogsBtn" style="padding: 6px 12px; font-size: 12px;">' + btnText + '</button></div>';
}

function toggleLogsPosition() {
    const progressLog = document.getElementById('progressLog');
    const logsPlaceholder = document.getElementById('logsPlaceholder');
    const mainApp = document.getElementById('mainApp');
    const statusDiv = document.getElementById('status');
    const toggleBtn = document.getElementById('toggleLogsBtn');
    
    if (!progressLog || !logsPlaceholder || !mainApp) return;
    
    const isAtTop = logsPlaceholder.contains(progressLog);
    
    if (isAtTop) {
        if (statusDiv && statusDiv.parentNode === mainApp) {
            mainApp.insertBefore(progressLog, statusDiv);
        } else {
            mainApp.appendChild(progressLog);
        }
        logsPlaceholder.style.display = 'none';
        if (toggleBtn) toggleBtn.textContent = '[ ↑ Move to Top ]';
        localStorage.setItem('consoleLogsPosition', 'bottom');
    } else {
        logsPlaceholder.appendChild(progressLog);
        logsPlaceholder.style.display = 'block';
        if (toggleBtn) toggleBtn.textContent = '[ ↓ Move to Bottom ]';
        localStorage.setItem('consoleLogsPosition', 'top');
    }
}

window.addEventListener('DOMContentLoaded', function() {
    const savedPosition = localStorage.getItem('consoleLogsPosition');
    if (savedPosition === 'top') {
        setTimeout(function() {
            const progressLog = document.getElementById('progressLog');
            const logsPlaceholder = document.getElementById('logsPlaceholder');
            const toggleBtn = document.getElementById('toggleLogsBtn');
            
            if (progressLog && logsPlaceholder) {
                logsPlaceholder.appendChild(progressLog);
                logsPlaceholder.style.display = 'block';
                if (toggleBtn) toggleBtn.textContent = '[ ↓ Move to Bottom ]';
            }
        }, 50);
    }
});

// Delete file/folder
function deleteFile(path) {
    if (!confirm(`Delete ${path}?`)) return;
    
    showLoading(true);
    fetch('/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: path})
    })
    .then(r => {
        showLoading(false);
        if (!r.ok) {
            return r.json().then(data => {
                throw new Error(data.error || 'Delete failed');
            });
        }
        return r.json();
    })
    .then(data => {
        if (data.success) {
            showStatus('Deleted successfully', 'success');
            logProgress(`[OK] Deleted: ${path}`, 'success');
            refreshFiles();
        } else {
            showStatus(data.error || 'Delete failed', 'error');
            logProgress(`[ERROR] Delete failed: ${data.error}`, 'error');
        }
    })
    .catch(err => {
        showLoading(false);
        showStatus('Error: ' + err.message, 'error');
        logProgress(`[ERROR] Delete failed: ${err.message}`, 'error');
    });
}

// Save settings
function saveSettings() {
    const settings = {
        password: document.getElementById('password').value,
        telegram_api_id: document.getElementById('telegram_api_id').value,
        telegram_api_hash: document.getElementById('telegram_api_hash').value,
        upload_destination: document.getElementById('upload_destination').value,
        upload_caption: document.getElementById('upload_caption').value,
        split_size_mb: parseInt(document.getElementById('split_size_mb').value) || 2000,
        video_keep_audio: document.getElementById('video_keep_audio').value === 'true',
        cpu_preset: document.getElementById('cpu_preset').value,
        cpu_threads: parseInt(document.getElementById('cpu_threads').value) || 0,
        parallel_connections: parseInt(document.getElementById('parallel_connections').value) || 20
    };
    
    fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(settings)
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showStatus('Settings saved!', 'success');
            logProgress('[OK] Settings saved successfully', 'success');
            
            loadChannels(true);
            checkFirstTimeSetup();
        } else {
            showStatus('Save failed', 'error');
            logProgress('[ERROR] Failed to save settings', 'error');
        }
    })
    .catch(err => {
        showStatus('Error: ' + err.message, 'error');
        logProgress('[ERROR] Error saving settings: ' + err.message, 'error');
    });
}

function showLogoutModal() {
    document.getElementById('logoutModal').style.display = 'flex';
}

function closeLogoutModal() {
    document.getElementById('logoutModal').style.display = 'none';
}

function confirmLogout() {
    showLoading(true);
    fetch('/logout', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(r => r.json())
    .then(data => {
        showLoading(false);
        closeLogoutModal();
        if (data.success) {
            showStatus('Logged out successfully', 'success');
            logProgress('[OK] Logged out from Telegram', 'success');
        } else {
            showStatus('Logout failed: ' + data.error, 'error');
            logProgress('[ERROR] Logout failed: ' + data.error, 'error');
        }
    })
    .catch(err => {
        showLoading(false);
        closeLogoutModal();
        showStatus('Error: ' + err.message, 'error');
        logProgress('[ERROR] Logout error: ' + err.message, 'error');
    });
}

function showResetModal() {
    document.getElementById('resetModal').style.display = 'flex';
}

function closeResetModal() {
    document.getElementById('resetModal').style.display = 'none';
}

function confirmReset() {
    showLoading(true);
    fetch('/reset', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
    })
    .then(r => r.json())
    .then(data => {
        showLoading(false);
        closeResetModal();
        if (data.success) {
            showStatus('Reset complete! Reloading...', 'success');
            logProgress('[OK] All settings reset to defaults', 'success');
            setTimeout(() => {
                window.location.reload();
            }, 1500);
        } else {
            showStatus('Reset failed: ' + data.error, 'error');
            logProgress('[ERROR] Reset failed: ' + data.error, 'error');
        }
    })
    .catch(err => {
        showLoading(false);
        closeResetModal();
        showStatus('Error: ' + err.message, 'error');
        logProgress('[ERROR] Reset error: ' + err.message, 'error');
    });
}

// UI helpers
function showLoading(show) {
    document.getElementById('loading').classList.toggle('active', show);
}

function showStatus(message, type) {
    const status = document.getElementById('status');
    status.textContent = `[ ${message} ]`;
    status.className = `status ${type}`;
    setTimeout(() => status.className = 'status', 5000);
}

// Load downloaded files
function refreshDownloaded() {
    fetch('/downloaded')
    .then(r => r.json())
    .then(folders => {
        const list = document.getElementById('downloadedList');
        
        if (folders.length === 0) {
            list.innerHTML = '<p style="color:#0f0;">No downloaded files</p>';
            return;
        }
        
        list.innerHTML = folders.map(folder => `
            <div class="folder-item">
                <div class="folder-info">
                    <h3>[ ${folder.display_name} ]</h3>
                    <p>${folder.files} file(s) • ${folder.size} GB • Downloaded: ${folder.created}</p>
                </div>
                <div class="folder-actions">
                    <button class="btn btn-danger" data-folder-path="${folder.name}">[ Delete ]</button>
                </div>
            </div>
        `).join('');
        
        // Add event listeners to delete buttons
        list.querySelectorAll('.btn-danger').forEach(btn => {
            btn.addEventListener('click', function() {
                const folderPath = this.getAttribute('data-folder-path');
                deleteFile(folderPath);
            });
        });
    });
}

// Initial load
refreshFiles();

// Load default upload destination on page load
document.addEventListener('DOMContentLoaded', function() {
    const processSelect = document.getElementById('uploadDestination');
    const settingsSelect = document.getElementById('upload_destination');
    
    // Restore checkbox states from localStorage
    const checkboxIds = ['processCompress', 'processBundleArchive', 'processEncrypt', 'processUpload'];
    checkboxIds.forEach(id => {
        const checkbox = document.getElementById(id);
        if (checkbox) {
            const savedState = localStorage.getItem(id);
            if (savedState !== null) {
                checkbox.checked = savedState === 'true';
            }
            
            // Save state when changed
            checkbox.addEventListener('change', function() {
                localStorage.setItem(id, this.checked);
            });
        }
    });
    
    // Fetch config from server to get the actual saved destination
    fetch('/settings')
    .then(r => r.json())
    .then(config => {
        const savedDest = config.upload_destination || 'me';
        
        // Set the default value in process dropdown immediately
        if (processSelect) {
            // Create a default option with the saved destination
            const defaultOption = document.createElement('option');
            defaultOption.value = savedDest;
            defaultOption.textContent = `Default: ${savedDest === 'me' ? 'Saved Messages' : savedDest}`;
            defaultOption.selected = true;
            processSelect.innerHTML = '';
            processSelect.appendChild(defaultOption);
            
            // Add "Click to load more" option
            const loadMoreOption = document.createElement('option');
            loadMoreOption.value = '__load__';
            loadMoreOption.textContent = '[ Click to load channels... ]';
            processSelect.appendChild(loadMoreOption);
        }
    })
    .catch(err => {
        console.error('Failed to load config:', err);
        // Fallback to 'me' if config fetch fails
        if (processSelect) {
            const defaultOption = document.createElement('option');
            defaultOption.value = 'me';
            defaultOption.textContent = 'Default: Saved Messages';
            defaultOption.selected = true;
            processSelect.innerHTML = '';
            processSelect.appendChild(defaultOption);
        }
    });
    
    // Load channels when user clicks on dropdown or selects "load more"
    if (processSelect) {
        processSelect.addEventListener('focus', function() {
            if (!channelsLoaded) {
                loadChannels();
            }
        });
        
        processSelect.addEventListener('change', function() {
            if (this.value === '__load__' && !channelsLoaded) {
                loadChannels();
            }
        });
    }
    
    if (settingsSelect) {
        settingsSelect.addEventListener('focus', function() {
            if (!channelsLoaded) {
                loadChannels();
            }
        });
    }
});

// Load available channels
let availableChannels = [];
let channelsLoaded = false; // Track if channels have been loaded

function loadChannels(forceRefresh = false) {
    const processSelect = document.getElementById('uploadDestination');
    const settingsSelect = document.getElementById('upload_destination');
    
    // Check cache first (unless force refresh)
    if (!forceRefresh && channelsLoaded) {
        return; // Already loaded, don't reload
    }
    
    if (!forceRefresh) {
        const cachedChannels = localStorage.getItem('telegram_channels');
        const cacheTime = localStorage.getItem('telegram_channels_time');
        
        // Use cache if it exists and is less than 1 hour old
        if (cachedChannels && cacheTime) {
            const age = Date.now() - parseInt(cacheTime);
            if (age < 3600000) { // 1 hour in milliseconds
                try {
                    const data = JSON.parse(cachedChannels);
                    populateChannelDropdowns(data);
                    channelsLoaded = true;
                    return; // Exit early - no network request needed
                } catch (e) {
                    // Invalid cache, fetch fresh data
                }
            }
        }
    }
    
    // Show loading state only when actually fetching from network
    processSelect.innerHTML = '<option value="">Loading channels...</option>';
    settingsSelect.innerHTML = '<option value="">Loading channels...</option>';
    
    // Fetch from server
    fetch('/telegram-channels')
    .then(r => {
        if (r.status === 401) {
            // Not logged in
            return r.json().then(data => {
                const processSelect = document.getElementById('uploadDestination');
                const settingsSelect = document.getElementById('upload_destination');
                processSelect.innerHTML = '<option value="">Not logged in to Telegram</option>';
                settingsSelect.innerHTML = '<option value="">Not logged in to Telegram</option>';
                availableChannels = [];
                // Clear cache
                localStorage.removeItem('telegram_channels');
                localStorage.removeItem('telegram_channels_time');
                return;
            });
        }
        return r.json();
    })
    .then(data => {
        if (!data) return; // Already handled 401
        
        if (data.success && data.channels) {
            // Cache the channels
            localStorage.setItem('telegram_channels', JSON.stringify(data));
            localStorage.setItem('telegram_channels_time', Date.now().toString());
            
            populateChannelDropdowns(data);
        } else {
            const processSelect = document.getElementById('uploadDestination');
            const settingsSelect = document.getElementById('upload_destination');
            processSelect.innerHTML = '<option value="">Error loading channels</option>';
            settingsSelect.innerHTML = '<option value="">Error loading channels</option>';
        }
    })
    .catch(err => {
        const processSelect = document.getElementById('uploadDestination');
        const settingsSelect = document.getElementById('upload_destination');
        processSelect.innerHTML = '<option value="">Error loading channels</option>';
        settingsSelect.innerHTML = '<option value="">Error loading channels</option>';
    });
}

function populateChannelDropdowns(data) {
    const processSelect = document.getElementById('uploadDestination');
    const settingsSelect = document.getElementById('upload_destination');
    
    availableChannels = data.channels;
    channelsLoaded = true; // Mark as loaded
    
    // Get the saved default from config (rendered by Flask)
    const savedDest = settingsSelect.getAttribute('data-saved-value') || 'me';
    
    // Update Process Files dropdown
    processSelect.innerHTML = '';
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'Use default from Settings';
    processSelect.appendChild(defaultOption);
    
    // Update Settings dropdown
    settingsSelect.innerHTML = '';
    
    // Add channels to both dropdowns
    data.channels.forEach(channel => {
        // Convert both to strings for comparison
        const channelIdStr = String(channel.id);
        const savedDestStr = String(savedDest);
        
        // Process Files dropdown
        const processOption = document.createElement('option');
        processOption.value = channel.id;
        processOption.textContent = `${channel.name} (${channel.id})`;
        if (channelIdStr === savedDestStr) {
            processOption.textContent += ' [Default]';
        }
        processSelect.appendChild(processOption);
        
        // Settings dropdown
        const settingsOption = document.createElement('option');
        settingsOption.value = channel.id;
        settingsOption.textContent = `${channel.name} (${channel.id})`;
        // Select the saved value
        if (channelIdStr === savedDestStr) {
            settingsOption.selected = true;
        }
        settingsSelect.appendChild(settingsOption);
    });
}

// Generate BIP39-style 12-word mnemonic
function generatePassphrase() {
    fetch('/generate-passphrase')
    .then(r => r.json())
    .then(data => {
        const passphrase = data.passphrase;
        
        // Set both password fields
        document.getElementById('wizard_password').value = passphrase;
        document.getElementById('wizard_password_confirm').value = passphrase;
        
        // Change password field to text type to show the passphrase
        document.getElementById('wizard_password').type = 'text';
        
        // Don't show popup - just inline message
        const passphraseDisplay = document.getElementById('passphraseDisplay');
        if (passphraseDisplay) {
            passphraseDisplay.textContent = `Generated: ${passphrase}`;
            passphraseDisplay.style.display = 'block';
        }
    })
    .catch(err => {
        // Failed to generate passphrase
    });
}

// Check if first-time setup is needed
function checkFirstTimeSetup() {
    const password = document.getElementById('password').value.trim();
    const apiId = document.getElementById('telegram_api_id').value.trim();
    const apiHash = document.getElementById('telegram_api_hash').value.trim();
    
    const hasPassword = password !== '';
    const hasApiId = apiId !== '';
    const hasApiHash = apiHash !== '';
    
    // Only show onboarding if credentials are missing
    // Don't check login status on every page load - too aggressive
    if (!hasPassword || !hasApiId || !hasApiHash) {
        document.getElementById('onboardingOverlay').style.display = 'block';
        document.getElementById('mainApp').style.display = 'none';
        wizardShowStep(1);
    } else {
        document.getElementById('onboardingOverlay').style.display = 'none';
        document.getElementById('mainApp').style.display = 'block';
    }
}

// Wizard navigation
function wizardShowStep(stepNum) {
    // Hide all steps
    for (let i = 1; i <= 4; i++) {
        const step = document.getElementById(`wizardStep${i}`);
        if (step) step.style.display = 'none';
    }
    // Show current step
    const currentStep = document.getElementById(`wizardStep${stepNum}`);
    if (currentStep) currentStep.style.display = 'block';
}

function wizardNextStep(stepNum) {
    // Hide any previous errors
    if (document.getElementById('wizardError2')) document.getElementById('wizardError2').style.display = 'none';
    if (document.getElementById('wizardError3')) document.getElementById('wizardError3').style.display = 'none';
    if (document.getElementById('wizardError4')) document.getElementById('wizardError4').style.display = 'none';
    
    // If going to step 4, try to save and load channels if credentials are provided
    if (stepNum === 4) {
        const apiId = document.getElementById('wizard_api_id').value.trim();
        const apiHash = document.getElementById('wizard_api_hash').value.trim();
        const password = document.getElementById('wizard_password').value;
        
        if (apiId && apiHash && password) {
            // Save credentials and load channels
            const tempSettings = {
                telegram_api_id: apiId,
                telegram_api_hash: apiHash,
                password: password
            };
            
            fetch('/settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(tempSettings)
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    wizardLoadChannels();
                }
            });
        } else {
            // If no credentials, just skip to main app
            skipOnboarding();
            return;
        }
    }
    
    wizardShowStep(stepNum);
}

function skipOnboarding() {
    // Save whatever they've entered (if anything)
    const apiId = document.getElementById('wizard_api_id').value.trim();
    const apiHash = document.getElementById('wizard_api_hash').value.trim();
    const password = document.getElementById('wizard_password').value;
    
    const settings = {
        telegram_api_id: apiId || '',
        telegram_api_hash: apiHash || '',
        password: password || '',
        upload_destination: 'me',
        split_size_mb: 2000,
        video_keep_audio: true,
        cpu_preset: 'normal',
        cpu_threads: 0,
        parallel_connections: 20,
        upload_caption: 'detailed'
    };
    
    fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(settings)
    }).then(r => r.json()).then(data => {
        // Copy to main settings
        document.getElementById('telegram_api_id').value = settings.telegram_api_id;
        document.getElementById('telegram_api_hash').value = settings.telegram_api_hash;
        document.getElementById('password').value = settings.password;
        
        // Hide overlay, show main app
        document.getElementById('onboardingOverlay').style.display = 'none';
        document.getElementById('mainApp').style.display = 'block';
        
        // Try to load channels if credentials exist
        if (apiId && apiHash) {
            loadChannels();
        }
    });
}

function wizardPrevStep(stepNum) {
    wizardShowStep(stepNum);
}

function wizardLoadChannels() {
    fetch('/telegram-channels')
    .then(r => r.json())
    .then(data => {
        const select = document.getElementById('wizard_destination');
        if (data.success && data.channels) {
            select.innerHTML = '';
            data.channels.forEach(channel => {
                const option = document.createElement('option');
                option.value = channel.id;
                option.textContent = `${channel.name} (${channel.id})`;
                select.appendChild(option);
            });
        } else {
            select.innerHTML = '<option value="me">Saved Messages (me)</option>';
        }
    })
    .catch(err => {
        const select = document.getElementById('wizard_destination');
        select.innerHTML = '<option value="me">Saved Messages (me)</option>';
    });
}

function wizardComplete() {
    const destination = document.getElementById('wizard_destination').value;
    
    if (!destination) {
        document.getElementById('wizardError4').textContent = 'Please select an upload destination';
        document.getElementById('wizardError4').style.display = 'block';
        return;
    }
    document.getElementById('wizardError4').style.display = 'none';
    
    // Save final settings
    const finalSettings = {
        telegram_api_id: document.getElementById('wizard_api_id').value,
        telegram_api_hash: document.getElementById('wizard_api_hash').value,
        password: document.getElementById('wizard_password').value,
        upload_destination: destination,
        split_size_mb: 2000,
        video_keep_audio: true,
        cpu_preset: 'normal',
        cpu_threads: 0,
        parallel_connections: 20,
        upload_caption: 'detailed'
    };
    
    fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(finalSettings)
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            // Copy wizard values to main settings
            document.getElementById('telegram_api_id').value = finalSettings.telegram_api_id;
            document.getElementById('telegram_api_hash').value = finalSettings.telegram_api_hash;
            document.getElementById('password').value = finalSettings.password;
            
            // Hide overlay, show main app
            document.getElementById('onboardingOverlay').style.display = 'none';
            document.getElementById('mainApp').style.display = 'block';
            
            // Load channels in main settings
            loadChannels();
            
            showStatus('Setup complete! Welcome to Tel Archive.', 'success');
        } else {
            document.getElementById('wizardError4').textContent = 'Setup failed. Please try again.';
            document.getElementById('wizardError4').style.display = 'block';
        }
    })
    .catch(err => {
        document.getElementById('wizardError4').textContent = 'Error: ' + err.message;
        document.getElementById('wizardError4').style.display = 'block';
    });
}

// Check on page load and when settings tab is opened
checkFirstTimeSetup();

// Check for active jobs on page load
window.addEventListener('load', () => {
    // Ask server if there's an active job
    fetch('/active-job')
    .then(r => r.json())
    .then(data => {
        if (data.job_id) {
            clearProgress();
            logProgress('[RESUME] Resuming active job...');
            startProgressPolling(data.job_id);
        }
    })
    .catch(() => {
        // No active job or error, ignore
    });
});

// Telegram login functions
let phoneCodeHash = null;

function sendTelegramCode() {
    const apiId = document.getElementById('wizard_api_id').value.trim();
    const apiHash = document.getElementById('wizard_api_hash').value.trim();
    const phone = document.getElementById('wizard_phone').value.trim();
    
    if (!apiId || !apiHash) {
        document.getElementById('wizardError2').textContent = 'Please enter API ID and Hash first';
        document.getElementById('wizardError2').style.display = 'block';
        return;
    }
    
    if (!phone) {
        document.getElementById('wizardError2').textContent = 'Please enter phone number';
        document.getElementById('wizardError2').style.display = 'block';
        return;
    }
    
    document.getElementById('wizardError2').style.display = 'none';
    
    // Save API credentials first
    fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            telegram_api_id: apiId,
            telegram_api_hash: apiHash
        })
    }).then(r => r.json()).then(data => {
        if (data.success) {
            // Now send code
            return fetch('/telegram-login-send-code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({phone: phone})
            });
        } else {
            throw new Error('Failed to save credentials');
        }
    }).then(r => r.json()).then(data => {
        if (data.success) {
            phoneCodeHash = data.phone_code_hash;
            document.getElementById('loginPhoneStep').style.display = 'none';
            document.getElementById('loginCodeStep').style.display = 'block';
            document.getElementById('wizardError2').style.display = 'none';
        } else {
            document.getElementById('wizardError2').textContent = data.error || 'Failed to send code';
            document.getElementById('wizardError2').style.display = 'block';
        }
    }).catch(err => {
        document.getElementById('wizardError2').textContent = 'Error: ' + err.message;
        document.getElementById('wizardError2').style.display = 'block';
    });
}

function verifyTelegramCode() {
    const phone = document.getElementById('wizard_phone').value.trim();
    const code = document.getElementById('wizard_code').value.trim();
    
    if (!code) {
        document.getElementById('wizardError2').textContent = 'Please enter verification code';
        document.getElementById('wizardError2').style.display = 'block';
        return;
    }
    
    document.getElementById('wizardError2').style.display = 'none';
    
    fetch('/telegram-login-verify', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            phone: phone,
            code: code,
            phone_code_hash: phoneCodeHash
        })
    }).then(r => r.json()).then(data => {
        if (data.success) {
            document.getElementById('loginCodeStep').style.display = 'none';
            document.getElementById('loginSuccess').style.display = 'block';
            document.getElementById('loginSuccess').textContent = `Logged in as ${data.user.name}!`;
            document.getElementById('wizardError2').style.display = 'none';
        } else {
            document.getElementById('wizardError2').textContent = data.error || 'Verification failed';
            document.getElementById('wizardError2').style.display = 'block';
        }
    }).catch(err => {
        document.getElementById('wizardError2').textContent = 'Error: ' + err.message;
        document.getElementById('wizardError2').style.display = 'block';
    });
}

function backToPhoneStep() {
    document.getElementById('loginCodeStep').style.display = 'none';
    document.getElementById('loginPhoneStep').style.display = 'block';
    document.getElementById('wizard_code').value = '';
    phoneCodeHash = null;
}

// Generate password modal functions
function showGeneratePasswordModal() {
    document.getElementById('generatePasswordModal').style.display = 'flex';
}

function closeGeneratePasswordModal() {
    document.getElementById('generatePasswordModal').style.display = 'none';
}

function confirmGeneratePassword() {
    const oldPassword = document.getElementById('password').value;
    
    // Generate new password
    fetch('/generate-passphrase')
    .then(r => r.json())
    .then(data => {
        const newPassword = data.passphrase;
        
        // Save old password to file if it exists
        if (oldPassword && oldPassword.trim() !== '') {
            fetch('/save-old-password', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({old_password: oldPassword})
            }).then(r => r.json()).then(saveData => {
                if (saveData.success) {
                    logProgress(`[OK] Old password saved to: ${saveData.file}`, 'success');
                }
            });
        }
        
        // Set new password
        document.getElementById('password').value = newPassword;
        document.getElementById('password').type = 'text'; // Show the password
        
        closeGeneratePasswordModal();
        
        showStatus('New password generated! Remember to save settings.', 'success');
        logProgress(`[OK] New password generated: ${newPassword}`, 'success');
    })
    .catch(err => {
        closeGeneratePasswordModal();
        showStatus('Error generating password: ' + err.message, 'error');
    });
}

// Raw upload warning modal functions
function showRawUploadWarning() {
    document.getElementById('rawUploadWarningModal').style.display = 'flex';
}

function closeRawUploadWarning() {
    document.getElementById('rawUploadWarningModal').style.display = 'none';
}

function confirmRawUpload() {
    // Save preference if checkbox is checked
    const dontShow = document.getElementById('dontShowRawWarning').checked;
    if (dontShow) {
        localStorage.setItem('dontShowRawWarning', 'true');
    }
    
    closeRawUploadWarning();
    
    // Set bypass flag and call processSelectedFiles again
    bypassRawWarning = true;
    processSelectedFiles();
}
