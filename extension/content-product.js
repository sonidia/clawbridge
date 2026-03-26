// Content script cho trang sản phẩm shopee.vn
// Hiển thị nút "COPY AFF LINK" và gửi request qua background tới affiliate tab

console.log('[PRODUCT] Content script loaded on product page');

function createButton() {
    if (document.getElementById('gemini-aff-btn')) return;

    var btn = document.createElement('div');
    btn.id = 'gemini-aff-btn';
    btn.innerHTML = '🔗 COPY AFF LINK';

    Object.assign(btn.style, {
        position: 'fixed',
        bottom: '100px',
        right: '20px',
        zIndex: '2147483647',
        backgroundColor: '#ff4500',
        color: 'white',
        padding: '15px 25px',
        borderRadius: '50px',
        cursor: 'pointer',
        fontWeight: 'bold',
        fontSize: '16px',
        boxShadow: '0 5px 15px rgba(0,0,0,0.5)',
        border: '2px solid white',
        display: 'block',
        textAlign: 'center',
        userSelect: 'none',
        transition: 'all 0.3s ease'
    });

    btn.onmouseenter = function() { btn.style.transform = 'scale(1.05)'; };
    btn.onmouseleave = function() { btn.style.transform = 'scale(1)'; };

    btn.onclick = function() {
        btn.innerHTML = '⏳ Đang chuẩn bị...';
        btn.style.backgroundColor = '#ffc107';
        btn.style.color = '#000';

        // Anti-detection: random delay 1-3s before sending request (human think time)
        var thinkDelay = 1000 + Math.random() * 2000;
        setTimeout(function() {
            btn.innerHTML = '⏳ Đang lấy link...';
        }, thinkDelay * 0.4);

        function resetBtn() {
            setTimeout(function() {
                btn.innerHTML = '🔗 COPY AFF LINK';
                btn.style.backgroundColor = '#ff4500';
                btn.style.color = 'white';
            }, 3000);
        }

        setTimeout(function() {
            chrome.runtime.sendMessage(
                { action: 'getAffLink', url: window.location.href },
                function(response) {
                    if (chrome.runtime.lastError) {
                        btn.innerHTML = '❌ Extension error';
                        btn.style.backgroundColor = '#dc3545';
                        btn.style.color = 'white';
                        console.error('[PRODUCT] Runtime error:', chrome.runtime.lastError);
                        alert('Extension error: ' + chrome.runtime.lastError.message);
                        resetBtn();
                        return;
                    }

                    if (!response) {
                        btn.innerHTML = '❌ No response';
                        btn.style.backgroundColor = '#dc3545';
                        btn.style.color = 'white';
                        alert('Không nhận được phản hồi!\nHãy đảm bảo tab affiliate.shopee.vn đang mở.');
                        resetBtn();
                        return;
                    }

                    if (response.status === 'done' && response.shortLink) {
                        // Copy to clipboard
                        navigator.clipboard.writeText(response.shortLink).then(function() {
                            btn.innerHTML = '✅ Đã copy!';
                            btn.style.backgroundColor = '#28a745';
                            btn.style.color = 'white';
                            console.log('[PRODUCT] Copied:', response.shortLink);
                        }).catch(function() {
                            // Fallback copy
                            var ta = document.createElement('textarea');
                            ta.value = response.shortLink;
                            document.body.appendChild(ta);
                            ta.select();
                            document.execCommand('copy');
                            document.body.removeChild(ta);
                            btn.innerHTML = '✅ Đã copy!';
                            btn.style.backgroundColor = '#28a745';
                            btn.style.color = 'white';
                        });
                        resetBtn();
                    } else if (response.status === 'error') {
                        btn.innerHTML = '❌ Lỗi';
                        btn.style.backgroundColor = '#dc3545';
                        btn.style.color = 'white';
                        alert('Lỗi: ' + (response.error || 'Unknown error'));
                        resetBtn();
                    } else {
                        btn.innerHTML = '❌ Unknown';
                        btn.style.backgroundColor = '#dc3545';
                        btn.style.color = 'white';
                        console.log('[PRODUCT] Unknown response:', response);
                        resetBtn();
                    }
                }
            );
        }, thinkDelay);
    };

    document.body.appendChild(btn);
}

// Kiểm tra liên tục mỗi 1 giây để đảm bảo nút luôn tồn tại
setInterval(createButton, 1000);
