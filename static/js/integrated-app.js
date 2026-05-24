/**
 * GraphRAG Web Application
 * Knowledge graph Q&A with session memory
 */

// Global state
let isRecording = false;
let recognition = null;
let queryHistory = [];
let currentSessionId = null;
let currentGraphData = null;
let currentQuestion = null;
let currentImageUrl = null;

// Mastery state
let allEntities = {};
let masteryState = {};
let knowledgeLoaded = false;

// ==================== Speech Recognition ====================

function initSpeechRecognition() {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'zh-CN';

        recognition.onstart = function() {
            isRecording = true;
            updateVoiceButton();
            showMessage('正在聆听...', 'info');
        };

        recognition.onresult = function(event) {
            const transcript = event.results[0][0].transcript;
            document.getElementById('questionInput').value = transcript;
            showMessage('识别成功: ' + transcript, 'success');
        };

        recognition.onerror = function(event) {
            console.error('语音识别错误:', event.error);
            showMessage('语音识别失败: ' + event.error, 'danger');
            isRecording = false;
            updateVoiceButton();
        };

        recognition.onend = function() {
            isRecording = false;
            updateVoiceButton();
        };
    } else {
        console.warn('浏览器不支持语音识别');
    }
}

function updateVoiceButton() {
    const voiceBtn = document.getElementById('voiceBtn');
    if (!voiceBtn) return;
    if (isRecording) {
        voiceBtn.classList.add('recording');
        voiceBtn.innerHTML = '<i class="fas fa-stop"></i>';
    } else {
        voiceBtn.classList.remove('recording');
        voiceBtn.innerHTML = '<i class="fas fa-microphone"></i>';
    }
}

function toggleVoiceRecognition() {
    if (!recognition) {
        showMessage('您的浏览器不支持语音识别功能', 'warning');
        return;
    }
    if (isRecording) {
        recognition.stop();
    } else {
        recognition.start();
    }
}

// ==================== Query History ====================

function loadQueryHistory() {
    const savedHistory = localStorage.getItem('graphrag_history');
    if (savedHistory) {
        queryHistory = JSON.parse(savedHistory);
        updateHistoryDisplay();
    }
}

function saveQueryHistory() {
    localStorage.setItem('graphrag_history', JSON.stringify(queryHistory));
}

function addToHistory(question, answer, processingTime) {
    const historyItem = {
        question: question,
        answer: answer,
        processingTime: processingTime || 0,
        timestamp: new Date().toISOString()
    };
    queryHistory.unshift(historyItem);
    if (queryHistory.length > 20) {
        queryHistory = queryHistory.slice(0, 20);
    }
    saveQueryHistory();
    updateHistoryDisplay();
}

function updateHistoryDisplay() {
    const historyList = document.getElementById('historyList');
    const historyContainer = document.getElementById('historyContainer');
    if (!historyList) return;

    if (queryHistory.length === 0) {
        historyList.innerHTML = `
            <div class="history-empty" id="historyContainer">
                <i class="far fa-comment-dots"></i>
                <span>暂无记录</span>
            </div>
        `;
        return;
    }

    historyList.innerHTML = '';
    queryHistory.forEach(function(item) {
        const el = document.createElement('div');
        el.className = 'history-item';

        const date = new Date(item.timestamp);
        const timeString = date.toLocaleString('zh-CN', {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });

        el.innerHTML = `
            <div class="history-question">${escapeHtml(item.question)}</div>
            <div class="history-time">${timeString}${item.processingTime ? ' · ' + item.processingTime.toFixed(1) + 's' : ''}</div>
        `;

        el.onclick = function() {
            const questionInput = document.getElementById('questionInput');
            if (questionInput) {
                questionInput.value = item.question;
                submitQuery();
            }
        };

        historyList.appendChild(el);
    });
}

function clearHistory() {
    if (confirm('确定要清空所有查询历史吗？')) {
        queryHistory = [];
        saveQueryHistory();
        updateHistoryDisplay();
        showMessage('历史记录已清空', 'success');
    }
}

// ==================== Toast Notifications ====================

function showMessage(message, type) {
    type = type || 'info';
    const messageContainer = document.getElementById('messageContainer');
    if (!messageContainer) return;

    const icons = {
        success: 'fas fa-check-circle',
        info: 'fas fa-info-circle',
        warning: 'fas fa-exclamation-circle',
        danger: 'fas fa-times-circle'
    };

    const toast = document.createElement('div');
    toast.className = 'toast-msg toast-' + type;
    toast.innerHTML = '<i class="' + (icons[type] || icons.info) + '"></i><span>' + message + '</span>';

    messageContainer.appendChild(toast);

    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(12px)';
        toast.style.transition = 'all 0.2s ease';
        setTimeout(function() { toast.remove(); }, 200);
    }, 4000);
}

// ==================== Markdown Formatting ====================

function formatAnswer(answer) {
    if (!answer) return '';

    var lines = answer.split('\n');
    var formattedLines = [];
    var inCodeBlock = false;
    var inList = false;
    var listType = null;

    for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        var trimmed = line.trim();

        if (trimmed.startsWith('```')) {
            if (inCodeBlock) {
                formattedLines.push('</code></pre>');
                inCodeBlock = false;
            } else {
                formattedLines.push('<pre class="bg-light p-3 rounded"><code>');
                inCodeBlock = true;
            }
            continue;
        }

        if (inCodeBlock) {
            formattedLines.push(escapeHtml(line));
            continue;
        }

        if (trimmed === '') {
            if (inList) {
                formattedLines.push(listType === 'ordered' ? '</ol>' : '</ul>');
                inList = false;
                listType = null;
            }
            formattedLines.push('<br>');
            continue;
        }

        if (trimmed.match(/^#{1,6}\s/)) {
            if (inList) {
                formattedLines.push(listType === 'ordered' ? '</ol>' : '</ul>');
                inList = false;
                listType = null;
            }
            var level = trimmed.match(/^(#+)/)[1].length;
            var title = trimmed.replace(/^#+\s/, '');
            formattedLines.push('<h' + Math.min(level + 2, 6) + ' class="mt-3 mb-2">' + processInlineFormatting(title) + '</h' + Math.min(level + 2, 6) + '>');
            continue;
        }

        if (trimmed.startsWith('> ')) {
            if (inList) {
                formattedLines.push(listType === 'ordered' ? '</ol>' : '</ul>');
                inList = false;
                listType = null;
            }
            var quote = trimmed.replace(/^>\s/, '');
            formattedLines.push('<blockquote class="border-left border-primary pl-3">' + processInlineFormatting(quote) + '</blockquote>');
            continue;
        }

        if (trimmed.match(/^\d+\.\s/)) {
            if (!inList || listType !== 'ordered') {
                if (inList) formattedLines.push('</ul>');
                formattedLines.push('<ol class="mb-3">');
                inList = true;
                listType = 'ordered';
            }
            var item = trimmed.replace(/^\d+\.\s/, '');
            formattedLines.push('<li>' + processInlineFormatting(item) + '</li>');
            continue;
        }

        if (trimmed.match(/^[-*•]\s/)) {
            if (!inList || listType !== 'unordered') {
                if (inList) formattedLines.push('</ol>');
                formattedLines.push('<ul class="mb-3">');
                inList = true;
                listType = 'unordered';
            }
            var item2 = trimmed.replace(/^[-*•]\s/, '');
            formattedLines.push('<li>' + processInlineFormatting(item2) + '</li>');
            continue;
        }

        if (inList) {
            formattedLines.push(listType === 'ordered' ? '</ol>' : '</ul>');
            inList = false;
            listType = null;
        }

        formattedLines.push('<p class="mb-2">' + processInlineFormatting(line) + '</p>');
    }

    if (inList) {
        formattedLines.push(listType === 'ordered' ? '</ol>' : '</ul>');
    }

    return formattedLines.join('');
}

function processInlineFormatting(text) {
    var formatted = text.replace(/`([^`]+)`/g, '<code class="bg-light px-1 rounded">$1</code>');
    formatted = formatted.replace(/~~([^~\n]+)~~/g, '<del>$1</del>');
    formatted = formatted.replace(/\*\*\*([^*\n]+)\*\*\*/g, '<strong><em>$1</em></strong>');
    formatted = formatted.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    formatted = formatted.replace(/__([^_\n]+)__/g, '<em>$1</em>');
    formatted = formatted.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    formatted = formatted.replace(/<strong><em>([^<]+)<\/em><\/strong>/g, '<strong>$1</strong>');
    formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="text-primary">$1</a>');
    return formatted;
}

function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==================== Utility ====================

function copyAnswer() {
    var answerContent = document.getElementById('answerContent');
    if (!answerContent) return;
    navigator.clipboard.writeText(answerContent.textContent).then(function() {
        showMessage('已复制到剪贴板', 'success');
    }).catch(function() {
        showMessage('复制失败', 'danger');
    });
}

function shareAnswer() {
    var answerContent = document.getElementById('answerContent');
    if (!answerContent) return;
    var text = answerContent.textContent;
    if (navigator.share) {
        navigator.share({ title: 'GraphRAG', text: text });
    } else {
        navigator.clipboard.writeText(text).then(function() {
            showMessage('已复制', 'success');
        });
    }
}

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', function(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            submitQuery();
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            var input = document.getElementById('questionInput');
            if (input) { input.value = ''; input.focus(); }
        }
        if (e.key === 'Escape' && isRecording) {
            recognition.stop();
        }
    });
}

function toggleTheme() {
    var body = document.body;
    var current = body.getAttribute('data-theme');
    var next = current === 'dark' ? 'light' : 'dark';
    body.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

function loadTheme() {
    var saved = localStorage.getItem('theme') || 'dark';
    document.body.setAttribute('data-theme', saved);
}

function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen();
    } else {
        document.exitFullscreen();
    }
}

// ==================== Graph Image ====================

function displayGraphImage(graphData, question) {
    var nodeCountEl = document.getElementById('graphNodeCount');
    var edgeCountEl = document.getElementById('graphEdgeCount');

    if (nodeCountEl) nodeCountEl.textContent = graphData && graphData.nodes ? graphData.nodes.length : 0;
    if (edgeCountEl) edgeCountEl.textContent = graphData && graphData.relationships ? graphData.relationships.length : 0;

    if (!graphData || !graphData.nodes || graphData.nodes.length === 0) {
        showGraphPlaceholder('未找到相关知识节点');
        return;
    }

    currentGraphData = graphData;
    currentQuestion = question;

    // Open panel and render graph
    openGraphPanel();

    var container = document.getElementById('d3GraphContainer');
    var placeholder = document.getElementById('graphPlaceholder');
    if (container) container.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';

    // Create graph instance if needed, then update
    if (!window._neo4jGraph) {
        setTimeout(function() {
            if (!window._neo4jGraph) {
                window._neo4jGraph = new Neo4jGraph('d3GraphContainer');
            }
            window._neo4jGraph.updateGraph(currentGraphData);
        }, 100);
    } else {
        window._neo4jGraph.updateGraph(graphData);
    }
}

function showGraphPlaceholder(message) {
    var el = document.getElementById('graphPlaceholder');
    if (el) {
        var p = el.querySelector('p');
        if (p && message) p.textContent = message;
        el.style.display = 'flex';
    }
    var container = document.getElementById('d3GraphContainer');
    if (container) container.style.display = 'none';
}

function showGraphLoading() {
    var el = document.getElementById('graphLoading');
    if (el) el.style.display = 'flex';
}

function showGraphImage(url) {
    currentImageUrl = url;
    var wrapper = document.getElementById('graphImageWrapper');
    var img = document.getElementById('graphImage');
    if (wrapper && img) {
        img.src = url;
        wrapper.style.display = 'flex';
    }
}

function openImageLightbox(src) {
    var lightbox = document.getElementById('imageLightbox');
    var img = document.getElementById('lightboxImage');
    if (lightbox && img) {
        img.src = src;
        lightbox.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeImageLightbox() {
    var lightbox = document.getElementById('imageLightbox');
    if (lightbox) {
        lightbox.classList.remove('active');
        document.body.style.overflow = '';
    }
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeImageLightbox();
});

function showGraphError(message) {
    console.error('Graph error:', message);
}

function hideAllGraphStates() {}

async function requestImageGeneration() {
    if (!currentGraphData || !currentQuestion) return;
    try {
        var response = await fetch('/api/generate_image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                graph_data: currentGraphData,
                question: currentQuestion
            })
        });
        if (response.ok) {
            var data = await response.json();
            if (data.image_url) {
                showGraphImage(data.image_url);
            }
        }
    } catch (error) {
        console.error('Image generation failed:', error);
    }
}

function regenerateImage() {
    if (!currentGraphData || !currentQuestion) return;
    requestImageGeneration();
}

function retryImageGeneration() { regenerateImage(); }

function downloadImage() {
    if (!currentImageUrl) return;
    var a = document.createElement('a');
    a.href = currentImageUrl;
    a.download = 'knowledge_graph_' + Date.now() + '.png';
    a.target = '_blank';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showMessage('下载已开始', 'success');
}

// ==================== Graph Stats ====================

async function loadGraphStats() {
    try {
        var response = await fetch('/api/graph_stats');
        var stats = await response.json();
        var nodeCount = stats.totalNodes || stats.entity_count || stats.node_count || 0;

        var totalEl = document.getElementById('totalNodes');
        if (totalEl) totalEl.textContent = nodeCount;

        var topbarEl = document.getElementById('topbarNodeCount');
        if (topbarEl) topbarEl.textContent = nodeCount;
    } catch (error) {
        console.error('加载统计信息失败:', error);
    }
}

// ==================== Search Suggestions ====================

function setupSearchSuggestions() {
    var input = document.getElementById('questionInput');
    var suggestions = document.getElementById('searchSuggestions');
    if (!input || !suggestions) return;

    var debounceTimer = null;

    input.addEventListener('input', function() {
        clearTimeout(debounceTimer);
        var query = this.value.trim();
        if (query.length < 2) {
            suggestions.style.display = 'none';
            return;
        }

        debounceTimer = setTimeout(async function() {
            try {
                var response = await fetch('/api/search_nodes?q=' + encodeURIComponent(query));
                var data = await response.json();
                if (data.nodes && data.nodes.length > 0) {
                    suggestions.innerHTML = '';
                    data.nodes.slice(0, 5).forEach(function(node) {
                        var item = document.createElement('div');
                        item.className = 'suggestion-item';
                        item.textContent = node.name + ' (' + node.type + ')';
                        item.onclick = function() {
                            input.value = '关于' + node.name + '的问题';
                            suggestions.style.display = 'none';
                            input.focus();
                        };
                        suggestions.appendChild(item);
                    });
                    suggestions.style.display = 'block';
                } else {
                    suggestions.style.display = 'none';
                }
            } catch (error) {
                suggestions.style.display = 'none';
            }
        }, 250);
    });

    document.addEventListener('click', function(e) {
        if (!input.contains(e.target) && !suggestions.contains(e.target)) {
            suggestions.style.display = 'none';
        }
    });
}

// ==================== Query Submission ====================

async function submitQuery() {
    var questionInput = document.getElementById('questionInput');
    if (!questionInput) return;

    var question = questionInput.value.trim();
    if (!question) return;

    // Hide welcome
    var welcome = document.getElementById('welcomeBlock');
    if (welcome) welcome.style.display = 'none';

    // Show thinking
    var thinking = document.getElementById('thinkingIndicator');
    if (thinking) thinking.style.display = 'flex';

    // Hide suggestions
    var suggestions = document.getElementById('searchSuggestions');
    if (suggestions) suggestions.style.display = 'none';

    // Add user message
    var chatMessages = document.getElementById('chatMessages');
    if (chatMessages) {
        var userMsg = document.createElement('div');
        userMsg.className = 'chat-msg chat-msg-user';
        userMsg.innerHTML = '<div class="msg-bubble msg-bubble-user">' + escapeHtml(question) + '</div>';
        chatMessages.appendChild(userMsg);
    }

    // Clear input
    questionInput.value = '';

    // Scroll to bottom
    var chatScroll = document.getElementById('chatScroll');
    if (chatScroll) chatScroll.scrollTop = chatScroll.scrollHeight;

    try {
        var requestBody = { question: question };
        if (currentSessionId) {
            requestBody.session_id = currentSessionId;
        }

        var response = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody)
        });

        var data = await response.json();

        if (response.ok) {
            if (data.session_id) {
                currentSessionId = data.session_id;
                updateSessionIndicator();
            }

            // Add AI message
            if (chatMessages) {
                var aiMsg = document.createElement('div');
                aiMsg.className = 'chat-msg chat-msg-ai';

                var badgesHtml = '<div class="msg-badges">';
                if (data.session_id) {
                    badgesHtml += '<span class="msg-badge msg-badge-session"><i class="fas fa-brain" style="font-size:10px"></i> 会话记忆</span>';
                }
                if (data.processing_time !== undefined) {
                    badgesHtml += '<span class="msg-mode">' + data.processing_time.toFixed(1) + 's</span>';
                }
                badgesHtml += '</div>';

                var formattedAnswer = formatAnswer(data.answer);

                var graphBtnHtml = '';
                if (data.graph_data) {
                    graphBtnHtml = '<button class="msg-graph-btn" onclick="openGraphPanel()">' +
                        '<i class="fas fa-diagram-project"></i>' +
                        '<span>查看知识图谱</span>' +
                        '<i class="fas fa-arrow-right"></i>' +
                        '</button>';
                }

                // Entity tags from extracted knowledge points
                var entityTagsHtml = '';
                if (data.extracted_entities && data.extracted_entities.length > 0) {
                    entityTagsHtml = '<div class="entity-tags">';
                    entityTagsHtml += '<span class="entity-tags-label"><i class="fas fa-tags"></i> 相关知识点：</span>';
                    data.extracted_entities.forEach(function(entityName) {
                        var isMastered = masteryState[entityName];
                        var cls = isMastered ? 'entity-tag mastered' : 'entity-tag unmastered';
                        var icon = isMastered ? '<i class="fas fa-check-circle"></i>' : '<i class="far fa-circle"></i>';
                        var safeName = entityName.replace(/'/g, "\\'").replace(/"/g, '&quot;');
                        entityTagsHtml += '<button class="' + cls + '" data-entity="' + escapeHtml(entityName) + '" onclick="toggleEntityMastery(\'' + safeName + '\')">' +
                            icon + ' ' + escapeHtml(entityName) + '</button>';
                    });
                    entityTagsHtml += '</div>';
                }

                aiMsg.innerHTML = '<div class="msg-avatar"><i class="fas fa-sparkles"></i></div>' +
                    '<div class="msg-bubble msg-bubble-ai">' +
                    '<div class="msg-content">' + badgesHtml + formattedAnswer + '</div>' +
                    entityTagsHtml +
                    graphBtnHtml +
                    '</div>';

                chatMessages.appendChild(aiMsg);

                // Store answerContent reference for copy
                aiMsg.querySelector('.msg-content').id = 'answerContent';

                if (data.graph_data) {
                    displayGraphImage(data.graph_data, data.question);
                }
            }

            addToHistory(data.question, data.answer, data.processing_time);

            // Refresh mastery state after query
            if (currentSessionId && data.extracted_entities && data.extracted_entities.length > 0) {
                loadMasteryState();
            }

        } else {
            throw new Error(data.error || '查询失败');
        }
    } catch (error) {
        console.error('查询失败:', error);
        if (chatMessages) {
            var errMsg = document.createElement('div');
            errMsg.className = 'chat-msg chat-msg-ai';
            errMsg.innerHTML = '<div class="msg-avatar"><i class="fas fa-exclamation-triangle"></i></div>' +
                '<div class="msg-bubble msg-bubble-ai"><div class="msg-content"><p style="color:var(--red)">' + (error.message || '查询失败，请稍后再试') + '</p></div></div>';
            chatMessages.appendChild(errMsg);
        }
    } finally {
        if (thinking) thinking.style.display = 'none';
        if (chatScroll) chatScroll.scrollTop = chatScroll.scrollHeight;
    }
}

// ==================== Session ====================

async function startNewSession() {
    try {
        var response = await fetch('/api/sessions/new', { method: 'POST' });
        if (response.ok) {
            var data = await response.json();
            currentSessionId = data.session_id;
        } else {
            currentSessionId = null;
        }
    } catch (error) {
        currentSessionId = null;
    }
    updateSessionIndicator();

    var questionInput = document.getElementById('questionInput');
    if (questionInput) questionInput.value = '';

    // Reset chat
    var chatMessages = document.getElementById('chatMessages');
    if (chatMessages) {
        var msgs = chatMessages.querySelectorAll('.chat-msg');
        msgs.forEach(function(m) { m.remove(); });
    }

    var welcome = document.getElementById('welcomeBlock');
    if (welcome) welcome.style.display = '';

    loadMasteryState();
    showMessage('已开始新会话', 'info');
}

function updateSessionIndicator() {
    var indicator = document.getElementById('sessionIndicator');
    if (!indicator) return;
    indicator.style.display = currentSessionId ? 'inline-flex' : 'none';
}

async function loadSessionHistory() {
    if (!currentSessionId) return;
    try {
        var response = await fetch('/api/sessions/' + currentSessionId + '/history');
        var data = await response.json();
        return data.history || [];
    } catch (error) {
        return [];
    }
}

async function loadSessions() {
    try {
        var response = await fetch('/api/sessions');
        var data = await response.json();
        var sessions = data.sessions || [];
        if (sessions.length > 0 && !currentSessionId) {
            currentSessionId = sessions[0].session_id;
            updateSessionIndicator();
        }
    } catch (error) {
        // silent
    }
}

async function deleteCurrentSession() {
    if (!currentSessionId) return;
    try {
        await fetch('/api/sessions/' + currentSessionId, { method: 'DELETE' });
    } catch (error) {
        // silent
    }
    currentSessionId = null;
    updateSessionIndicator();
}

async function _expandNodeNeighbors(nodeName) {
    try {
        var response = await fetch('/api/node_neighbors/' + encodeURIComponent(nodeName));
        if (!response.ok) return;
        var data = await response.json();
        var neighbors = data.neighbors || [];
        if (!neighbors.length || !window._neo4jGraph || !currentGraphData) return;

        var existingNames = new Set(currentGraphData.nodes.map(function(n) { return n.name; }));
        var newNodes = [];
        var newNodeNames = new Set();

        neighbors.forEach(function(nb) {
            var name = nb.name;
            if (name && !existingNames.has(name)) {
                newNodes.push({
                    name: name,
                    type: nb.type || nb.entity_type || 'Entity',
                    description: nb.description || ''
                });
                newNodeNames.add(name);
            }
        });

        if (!newNodes.length) {
            showMessage('该节点没有更多邻居', 'info');
            return;
        }

        newNodes.forEach(function(n) { currentGraphData.nodes.push(n); });
        var allNames = new Set(currentGraphData.nodes.map(function(n) { return n.name; }));
        newNodeNames.add(nodeName);

        var allNamesArr = Array.from(allNames);
        currentGraphData.relationships = currentGraphData.relationships.filter(function(r) {
            var sn = typeof r.start === 'object' ? r.start.name : r.start;
            var en = typeof r.end === 'object' ? r.end.name : r.end;
            return allNames.has(sn) && allNames.has(en);
        });

        window._neo4jGraph.updateGraph(currentGraphData);
        showMessage('已展开 ' + newNodes.length + ' 个邻居节点', 'success');

        var nodeCountEl = document.getElementById('graphNodeCount');
        var edgeCountEl = document.getElementById('graphEdgeCount');
        if (nodeCountEl) nodeCountEl.textContent = currentGraphData.nodes.length;
        if (edgeCountEl) edgeCountEl.textContent = currentGraphData.relationships.length;
    } catch (error) {
        console.error('展开邻居失败:', error);
    }
}

async function exportSubgraph() {
    var questionInput = document.getElementById('questionInput');
    var query = questionInput ? questionInput.value.trim() : '';
    if (!query && currentQuestion) query = currentQuestion;
    if (!query) { showMessage('请先输入查询内容', 'warning'); return; }

    try {
        var response = await fetch('/api/export_graph', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: query })
        });
        if (!response.ok) throw new Error('导出失败');
        var data = await response.json();

        var blob = new Blob([JSON.stringify(data.graph_data, null, 2)], { type: 'application/json' });
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'subgraph_' + Date.now() + '.json';
        a.click();
        URL.revokeObjectURL(url);
        showMessage('子图数据已导出', 'success');
    } catch (error) {
        showMessage('导出失败: ' + error.message, 'danger');
    }
}

// ==================== Display Answer (legacy compat) ====================

function displayAnswer(data) {
    // This function is kept for backward compatibility but submitQuery
    // now handles message rendering directly
    console.log('displayAnswer called (legacy compat)');
}

// ==================== Helpers ====================

function askQuestion(question) {
    var questionInput = document.getElementById('questionInput');
    if (questionInput) {
        questionInput.value = question;
        submitQuery();
    }
}

function handleKeyPress(event) {
    if (event.key === 'Enter') {
        submitQuery();
    }
}

function checkBrowserCompatibility() {
    // Silent check, no user-facing output
}

// ==================== Init ====================

document.addEventListener('DOMContentLoaded', function() {
    initSpeechRecognition();
    loadQueryHistory();
    loadTheme();
    setupKeyboardShortcuts();
    loadGraphStats();
    setupSearchSuggestions();
    loadSessions();
    loadKnowledgeDirectory();
    checkBrowserCompatibility();
});

// Exports
window.toggleVoiceRecognition = toggleVoiceRecognition;
window.clearHistory = clearHistory;
window.copyAnswer = copyAnswer;
window.shareAnswer = shareAnswer;
window.toggleTheme = toggleTheme;
window.toggleFullscreen = toggleFullscreen;
window.displayGraphImage = displayGraphImage;
window.regenerateImage = regenerateImage;
window.retryImageGeneration = retryImageGeneration;
window.downloadImage = downloadImage;
window.formatAnswer = formatAnswer;
window.openImageLightbox = openImageLightbox;
window.closeImageLightbox = closeImageLightbox;
window.processInlineFormatting = processInlineFormatting;
window.loadGraphStats = loadGraphStats;
window.setupSearchSuggestions = setupSearchSuggestions;
window.askQuestion = askQuestion;
window.handleKeyPress = handleKeyPress;
window.submitQuery = submitQuery;
window.displayAnswer = displayAnswer;
window.startNewSession = startNewSession;
window.loadSessionHistory = loadSessionHistory;
window.loadSessions = loadSessions;
window.deleteCurrentSession = deleteCurrentSession;
window._expandNodeNeighbors = _expandNodeNeighbors;
window.exportSubgraph = exportSubgraph;
window.toggleEntityMastery = toggleEntityMastery;
window.switchSidebarTab = switchSidebarTab;
window.toggleLayerSection = toggleLayerSection;


// ==================== Knowledge Mastery ====================

async function loadKnowledgeDirectory() {
    try {
        var response = await fetch('/api/entities');
        var data = await response.json();
        allEntities = data.entities || {};
        document.getElementById('totalCount').textContent = data.total || 0;
        knowledgeLoaded = true;
        renderKnowledgeDirectory();
    } catch (error) {
        console.error('加载知识点目录失败:', error);
    }
}

async function loadMasteryState() {
    if (!currentSessionId) return;
    try {
        var response = await fetch('/api/mastery/' + currentSessionId);
        var data = await response.json();
        masteryState = data.mastery || {};
        renderKnowledgeDirectory();
    } catch (error) {
        console.error('加载掌握状态失败:', error);
    }
}

async function toggleEntityMastery(entityName) {
    if (!currentSessionId) {
        showMessage('请先开始一个会话', 'warning');
        return;
    }
    var newState = !masteryState[entityName];
    try {
        await fetch('/api/mastery', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: currentSessionId,
                entity_name: entityName,
                mastered: newState
            })
        });
        masteryState[entityName] = newState;
        renderKnowledgeDirectory();
        updateEntityTagInChat(entityName, newState);
    } catch (error) {
        showMessage('更新掌握状态失败', 'danger');
    }
}

function renderKnowledgeDirectory() {
    var container = document.getElementById('knowledgeLayers');
    if (!container || !knowledgeLoaded) return;

    container.innerHTML = '';
    var layerOrder = ['物理层', '数据链路层', '网络层', '运输层', '应用层'];

    layerOrder.forEach(function(layerName) {
        var entities = allEntities[layerName] || [];
        if (!entities.length) return;

        var section = document.createElement('div');
        section.className = 'knowledge-layer';

        var masteredCount = entities.filter(function(e) {
            return masteryState[e.name];
        }).length;

        var entitiesHtml = entities.map(function(e) {
            var m = masteryState[e.name];
            var safeName = e.name.replace(/'/g, "\\'");
            return '<div class="entity-item ' + (m ? 'mastered' : 'unmastered') +
                '" onclick="toggleEntityMastery(\'' + safeName + '\')">' +
                '<span class="entity-status"><i class="fas ' + (m ? 'fa-check-circle' : 'fa-circle') + '"></i></span>' +
                '<span class="entity-name">' + escapeHtml(e.name) + '</span>' +
                '<span class="entity-type-badge">' + e.entity_type + '</span>' +
                '</div>';
        }).join('');

        section.innerHTML =
            '<div class="layer-header" onclick="toggleLayerSection(this)">' +
            '  <span class="layer-name">' + layerName + '</span>' +
            '  <span class="layer-progress">' + masteredCount + '/' + entities.length + '</span>' +
            '  <i class="fas fa-chevron-down"></i>' +
            '</div>' +
            '<div class="layer-entities">' + entitiesHtml + '</div>';

        container.appendChild(section);
    });

    updateMasterySummary();
}

function updateMasterySummary() {
    var total = 0;
    var mastered = 0;
    Object.keys(allEntities).forEach(function(layer) {
        allEntities[layer].forEach(function(e) {
            total++;
            if (masteryState[e.name]) mastered++;
        });
    });

    var countEl = document.getElementById('masteredCount');
    var totalEl = document.getElementById('totalCount');
    var fillEl = document.getElementById('masteryBarFill');
    if (countEl) countEl.textContent = mastered;
    if (totalEl) totalEl.textContent = total;
    if (fillEl) fillEl.style.width = (total > 0 ? (mastered / total * 100) : 0) + '%';
}

function updateEntityTagInChat(entityName, mastered) {
    var tags = document.querySelectorAll('.entity-tag');
    tags.forEach(function(tag) {
        if (tag.getAttribute('data-entity') === entityName) {
            tag.className = 'entity-tag ' + (mastered ? 'mastered' : 'unmastered');
            tag.setAttribute('data-entity', entityName);
            var icon = tag.querySelector('i');
            if (icon) {
                icon.className = mastered ? 'fas fa-check-circle' : 'far fa-circle';
            }
        }
    });
}

function switchSidebarTab(tab) {
    var historySection = document.getElementById('historySection');
    var knowledgePanel = document.getElementById('knowledgePanel');
    var tabHistory = document.getElementById('tabHistory');
    var tabKnowledge = document.getElementById('tabKnowledge');

    if (tab === 'history') {
        historySection.style.display = '';
        knowledgePanel.style.display = 'none';
        tabHistory.classList.add('active');
        tabKnowledge.classList.remove('active');
    } else {
        historySection.style.display = 'none';
        knowledgePanel.style.display = 'flex';
        tabHistory.classList.remove('active');
        tabKnowledge.classList.add('active');
        if (!knowledgeLoaded) loadKnowledgeDirectory();
        if (currentSessionId && Object.keys(masteryState).length === 0) loadMasteryState();
    }
}

function toggleLayerSection(header) {
    var entities = header.nextElementSibling;
    var icon = header.querySelector('i');
    if (entities.style.display === 'none') {
        entities.style.display = '';
        icon.className = 'fas fa-chevron-down';
    } else {
        entities.style.display = 'none';
        icon.className = 'fas fa-chevron-right';
    }
}
