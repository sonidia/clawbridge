// Content script cho affiliate.shopee.vn
// Chỉ làm vai trò trung gian: nhận kết quả từ MAIN world (postMessage) → gửi về background

console.log('[AFF-CS] Content script loaded on affiliate page');

// Lắng nghe kết quả từ MAIN world script (injected by background via chrome.scripting.executeScript)
window.addEventListener('message', function(e) {
    if (e.data && e.data.type === '__aff_result') {
        console.log('[AFF-CS] Got result from MAIN world:', e.data.status);
        chrome.runtime.sendMessage({
            action: 'affResult',
            status: e.data.resultStatus,
            shortLink: e.data.shortLink || '',
            longLink: e.data.longLink || '',
            failCode: e.data.failCode,
            error: e.data.error || ''
        });
    }
});

// Badge trạng thái
var badge = document.createElement('div');
badge.id = 'aff-ext-badge';
badge.innerHTML = '✅ Shopee Aff Extension Active';
Object.assign(badge.style, {
    position: 'fixed', bottom: '10px', right: '10px', zIndex: '2147483647',
    backgroundColor: '#28a745', color: 'white', padding: '8px 15px',
    borderRadius: '20px', fontSize: '12px', fontWeight: 'bold',
    boxShadow: '0 2px 8px rgba(0,0,0,0.3)'
});
document.body.appendChild(badge);
