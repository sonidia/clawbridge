// Background service worker - 协调 product page 和 affiliate page 之间的通信
// 使用 chrome.scripting.executeScript + world: MAIN 绕过 CSP

var pendingRequest = null;

chrome.runtime.onMessage.addListener(function(message, sender, sendResponse) {
    if (message.action === 'getAffLink') {
        handleGetAffLink(message.url, sender.tab.id, sendResponse);
        return true; // Keep channel open for async response
    }

    if (message.action === 'affResult') {
        // Result from affiliate content script → relay to product tab
        console.log('[BG] Got affResult:', message.status, message.shortLink);
        if (pendingRequest && pendingRequest.sendResponse) {
            try {
                pendingRequest.sendResponse(message);
            } catch(e) {
                console.error('[BG] Error sending response:', e);
            }
            pendingRequest = null;
        }
    }
});

// Anti-detection: random delay between min and max milliseconds
function humanDelay(minMs, maxMs) {
    var delay = minMs + Math.random() * (maxMs - minMs);
    // Gaussian-like distribution (sum of 3 randoms / 3 for bell curve)
    var r = (Math.random() + Math.random() + Math.random()) / 3;
    delay = minMs + r * (maxMs - minMs);
    return new Promise(function(resolve) { setTimeout(resolve, Math.round(delay)); });
}

// Rate limiting: track last request time to avoid rapid-fire calls
var lastRequestTime = 0;

// 在 MAIN world 中执行的函数 - 可以访问页面的 XHR（SDK 会自动添加安全 headers）
function makeAffRequestInPage(productUrl) {
    // Anti-detection: random delay 1.5-4s before making API call (human browsing behavior)
    var delay = 1500 + Math.random() * 2500;

    setTimeout(function() {
        var payload = {
            "operationName": "batchGetCustomLink",
            "query": "\n    query batchGetCustomLink($linkParams: [CustomLinkParam!], $sourceCaller: SourceCaller){\n      batchCustomLink(linkParams: $linkParams, sourceCaller: $sourceCaller){\n        shortLink\n        longLink\n        failCode\n      }\n    }\n    ",
            "variables": {
                "linkParams": [{ "originalLink": productUrl, "advancedLinkParams": {} }],
                "sourceCaller": "CUSTOM_LINK_CALLER"
            }
        };

        // 从 cookie 获取 CSRF token
        var csrf = '';
        document.cookie.split(';').forEach(function(c) {
            c = c.trim();
            if (c.indexOf('_csrf=') === 0) csrf = c.substring(6);
        });

        var xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/v3/gql?q=batchCustomLink', true);
        xhr.setRequestHeader('Content-Type', 'application/json; charset=UTF-8');
        xhr.setRequestHeader('Accept', 'application/json, text/plain, */*');
        xhr.setRequestHeader('Accept-Language', 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7');
        xhr.setRequestHeader('Affiliate-Program-Type', '1');
        xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        if (csrf) {
            xhr.setRequestHeader('CSRF-token', csrf);
        }

        xhr.onreadystatechange = function() {
            if (xhr.readyState !== 4) return;
            try {
                var resData = JSON.parse(xhr.responseText);
                if (resData.data && resData.data.batchCustomLink && resData.data.batchCustomLink[0]) {
                    var result = resData.data.batchCustomLink[0];
                    window.postMessage({
                        type: '__aff_result',
                        resultStatus: 'done',
                        shortLink: result.shortLink || '',
                        longLink: result.longLink || '',
                        failCode: result.failCode
                    }, '*');
                } else {
                    window.postMessage({
                        type: '__aff_result',
                        resultStatus: 'error',
                        error: 'API response: ' + xhr.responseText.substring(0, 300)
                    }, '*');
                }
            } catch(e) {
                window.postMessage({
                    type: '__aff_result',
                    resultStatus: 'error',
                    error: 'Parse error: ' + e.message + ' | Status: ' + xhr.status + ' | Body: ' + xhr.responseText.substring(0, 200)
                }, '*');
            }
        };

        xhr.onerror = function() {
            window.postMessage({
                type: '__aff_result',
                resultStatus: 'error',
                error: 'XHR network error'
            }, '*');
        };

        xhr.send(JSON.stringify(payload));
    }, delay);
}

// Helper: wait for a tab to finish loading
function waitForTabComplete(tabId, timeoutMs) {
    return new Promise(function(resolve, reject) {
        var timer = setTimeout(function() {
            chrome.tabs.onUpdated.removeListener(listener);
            reject(new Error('Tab load timeout after ' + timeoutMs + 'ms'));
        }, timeoutMs || 15000);

        function listener(updatedTabId, changeInfo) {
            if (updatedTabId === tabId && changeInfo.status === 'complete') {
                clearTimeout(timer);
                chrome.tabs.onUpdated.removeListener(listener);
                // Extra delay for page JS to initialize
                setTimeout(function() { resolve(); }, 2000);
            }
        }

        // Check if already complete
        chrome.tabs.get(tabId, function(tab) {
            if (tab && tab.status === 'complete') {
                clearTimeout(timer);
                resolve();
            } else {
                chrome.tabs.onUpdated.addListener(listener);
            }
        });
    });
}

async function handleGetAffLink(productUrl, productTabId, sendResponse) {
    console.log('[BG] Request for aff link:', productUrl);

    // Anti-detection: rate limiting - minimum 5s between requests
    var now = Date.now();
    var timeSinceLast = now - lastRequestTime;
    var minCooldown = 5000 + Math.random() * 3000; // 5-8s cooldown
    if (timeSinceLast < minCooldown) {
        var waitMs = Math.round(minCooldown - timeSinceLast);
        console.log('[BG] Rate limiting: waiting ' + waitMs + 'ms before next request');
        await humanDelay(waitMs, waitMs + 1000);
    }
    lastRequestTime = Date.now();

    // 找到已打开的 affiliate tab，如果没有就自动打开一个
    var affTab = null;
    var isNewTab = false;
    try {
        var tabs = await chrome.tabs.query({ url: 'https://affiliate.shopee.vn/*' });
        if (tabs.length > 0) {
            affTab = tabs[0];
            console.log('[BG] Found existing affiliate tab:', affTab.id);
        }
    } catch(e) {
        console.error('[BG] Error querying tabs:', e);
    }

    // Auto-open affiliate tab if not found
    if (!affTab) {
        console.log('[BG] No affiliate tab found, auto-opening...');
        try {
            affTab = await chrome.tabs.create({
                url: 'https://affiliate.shopee.vn/offer/custom_link',
                active: false  // Open in background, don't switch focus
            });
            isNewTab = true;
            console.log('[BG] Created new affiliate tab:', affTab.id);

            // Wait for the tab to fully load
            await waitForTabComplete(affTab.id, 20000);
            console.log('[BG] Affiliate tab loaded');
        } catch(e) {
            console.error('[BG] Failed to create affiliate tab:', e);
            sendResponse({
                status: 'error',
                error: 'Không thể mở tab Affiliate tự động: ' + e.message + '\nHãy đăng nhập affiliate.shopee.vn trước.'
            });
            return;
        }
    }

    // Store pending request
    var requestId = Date.now() + '_' + Math.random();
    pendingRequest = {
        id: requestId,
        productTabId: productTabId,
        url: productUrl,
        sendResponse: sendResponse
    };

    // Set timeout 20s (longer for auto-opened tabs)
    var timeout = isNewTab ? 25000 : 15000;
    setTimeout(function() {
        if (pendingRequest && pendingRequest.id === requestId) {
            try {
                pendingRequest.sendResponse({
                    status: 'error',
                    error: 'Timeout! Hãy đảm bảo đã đăng nhập affiliate.shopee.vn.'
                });
            } catch(e) {}
            pendingRequest = null;
        }
    }, timeout);

    // 用 chrome.scripting.executeScript 在 MAIN world 中执行代码（绕过 CSP）
    try {
        // Inject the content-affiliate.js listener first if this is a new tab
        if (isNewTab) {
            await chrome.scripting.executeScript({
                target: { tabId: affTab.id },
                files: ['content-affiliate.js']
            });
            console.log('[BG] Injected content-affiliate.js into new tab');
        }

        await chrome.scripting.executeScript({
            target: { tabId: affTab.id },
            world: 'MAIN',
            func: makeAffRequestInPage,
            args: [productUrl]
        });
        console.log('[BG] Script injected into affiliate tab');
    } catch(e) {
        console.error('[BG] executeScript error:', e);
        sendResponse({
            status: 'error',
            error: 'Không thể inject script vào affiliate tab: ' + e.message
        });
        pendingRequest = null;
    }
}
