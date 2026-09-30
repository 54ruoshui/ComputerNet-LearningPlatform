/**
 * GraphRAG 刷题测验前端
 * - 可折叠章节侧边栏（DeepSeek 风格）
 * - 已做题目不重复出题
 * - 错题自动收录错题集
 * - 键盘快捷键 A-D 选择，Enter 提交
 */
(function () {
    "use strict";

    // --- 状态 ---
    let questions = [];
    let currentIdx = 0;
    let selectedAnswer = null;
    let answered = false;
    let score = { correct: 0, wrong: 0 };
    let currentTab = "quiz";
    let currentChapter = "";

    let _doneCache = null;
    let _wrongCache = null;

    const $ = (id) => document.getElementById(id);

    // ==================== API ====================

    async function apiGet(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    async function apiPost(url, body) {
        const opts = { method: "POST", headers: { "Content-Type": "application/json" } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    async function apiDelete(url, body) {
        const opts = { method: "DELETE", headers: { "Content-Type": "application/json" } };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    // ==================== Done IDs ====================

    async function getDoneIds() {
        if (_doneCache !== null) return _doneCache;
        try { const data = await apiGet("/api/quiz/done"); _doneCache = data.ids || []; }
        catch { _doneCache = []; }
        return _doneCache;
    }

    async function markDone(id) {
        _doneCache = null;
        return apiPost("/api/quiz/done/" + encodeURIComponent(id));
    }

    // ==================== Wrong List ====================

    async function getWrongList() {
        if (_wrongCache !== null) return _wrongCache;
        try { const data = await apiGet("/api/quiz/wrong"); _wrongCache = data.list || []; }
        catch { _wrongCache = []; }
        return _wrongCache;
    }

    async function addWrong(q, selected, result) {
        _wrongCache = null;
        return apiPost("/api/quiz/wrong", {
            id: q.id, text: q.text,
            option_a: q.option_a, option_b: q.option_b,
            option_c: q.option_c, option_d: q.option_d,
            correct_answer: result.correct_answer,
            explanation: result.explanation,
            layer: q.layer, difficulty: q.difficulty,
            selected: selected, time: new Date().toISOString()
        });
    }

    async function removeWrong(id) {
        _wrongCache = null;
        return apiDelete("/api/quiz/wrong/" + encodeURIComponent(id));
    }

    // ==================== 侧边栏折叠（桌面端） ====================

    function isMobile() { return window.innerWidth <= 860; }

    window.toggleSidebarCollapse = function () {
        const sidebar = $("quizSidebar");
        const collapsed = sidebar.classList.toggle("collapsed");
        localStorage.setItem("quiz_sidebar_collapsed", collapsed ? "1" : "0");
        // 收起后如果有展开的章节，保持展开以便下次展开侧边栏时可见
    };

    // ==================== 侧边栏开关（移动端） ====================

    window.toggleMobileSidebar = function () {
        if (!isMobile()) {
            // 桌面端：折叠/展开侧边栏
            toggleSidebarCollapse();
            return;
        }
        const sidebar = $("quizSidebar");
        const overlay = $("sidebarOverlay");
        const open = sidebar.classList.toggle("mobile-open");
        overlay.classList.toggle("show", open);
    };

    window.closeMobileSidebar = function () {
        $("quizSidebar").classList.remove("mobile-open");
        $("sidebarOverlay").classList.remove("show");
    };

    // 点击章节后移动端自动关闭侧边栏
    const _origSelectChapter = window.selectChapter;
    window.selectChapter = function (chapter, sectionName) {
        if (typeof _origSelectChapter === "function") _origSelectChapter(chapter, sectionName);
        currentChapter = chapter;
        // 更新侧边栏高亮
        document.querySelectorAll(".section-item").forEach(el => {
            el.classList.toggle("active", el.dataset.chapter === chapter);
        });
        document.querySelectorAll(".sidebar-all-btn").forEach(el => {
            el.classList.toggle("active", chapter === "");
        });
        // 更新标签
        $("currentChapterLabel").textContent = chapter ? (sectionName || chapter) : "全部题目";
        // 自动展开对应章节
        if (chapter) {
            const chNum = chapter.split(".")[0];
            const list = $("ch_" + chNum);
            const toggle = list ? list.previousElementSibling : null;
            if (list && !list.classList.contains("open")) {
                list.classList.add("open");
                if (toggle) toggle.classList.add("open");
            }
        }
        resetQuizState();
        if (isMobile()) closeMobileSidebar();
    };

    // ==================== 章节侧边栏 ====================

    async function loadChapterStructure() {
        try {
            const chapters = await apiGet("/api/quiz/chapters");
            renderChapterSidebar(chapters);
        } catch (e) {
            $("chapterGroups").innerHTML =
                '<div style="padding:16px;color:var(--text-3);font-size:12px;">加载失败</div>';
        }
    }

    function renderChapterSidebar(chapters) {
        let html = "";
        let hasAnyQuestion = false;
        for (const ch of chapters) {
            if (ch.count === 0 && ch.sections.every(s => s.count === 0)) continue;
            hasAnyQuestion = true;
            const hasContent = ch.sections.some(s => s.count > 0);
            html += `<div class="chapter-group">
                <button class="chapter-toggle${hasContent ? '' : ' empty'}"
                        onclick="toggleChapter(this, 'ch_${ch.chapter}')"
                        title="第${ch.chapter}章 ${ch.title}">
                    <i class="fas fa-chevron-right"></i>
                    <span>第${ch.chapter}章 ${ch.title}</span>
                    <span class="ch-count">${ch.count}</span>
                </button>
                <div class="section-list" id="ch_${ch.chapter}">`;
            for (const sec of ch.sections) {
                html += `
                    <button class="section-item${sec.chapter === currentChapter ? ' active' : ''}"
                            onclick="selectChapter('${sec.chapter}', '${sec.section.replace(/'/g, "\\'")}')"
                            data-chapter="${sec.chapter}">
                        <span>${sec.chapter} ${sec.section}</span>
                        ${sec.count > 0 ? `<span class="sec-count">${sec.count}</span>` : ''}
                    </button>`;
            }
            html += `</div></div>`;
        }
        if (!hasAnyQuestion) {
            html = '<div class="sidebar-empty">暂无题目，请先添加</div>';
        }
        $("chapterGroups").innerHTML = html;
    }

    window.toggleChapter = function (btn, listId) {
        const list = $(listId);
        const isOpen = list.classList.contains("open");
        document.querySelectorAll(".section-list.open").forEach(l => l.classList.remove("open"));
        document.querySelectorAll(".chapter-toggle.open").forEach(b => b.classList.remove("open"));
        if (!isOpen) {
            list.classList.add("open");
            btn.classList.add("open");
        }
    };

    // ==================== 刷题流程 ====================

    window.startQuiz = async function () {
        const layer = $("layerSelect").value;
        const diff = $("diffSelect").value;
        let url = "/api/quiz/questions?limit=50";
        if (layer) url += `&layer=${encodeURIComponent(layer)}`;
        if (diff) url += `&difficulty=${encodeURIComponent(diff)}`;
        if (currentChapter) url += `&chapter=${encodeURIComponent(currentChapter)}`;

        try {
            $("startBtn").disabled = true;
            const data = await apiGet(url);
            const all = data.questions || [];

            const doneIds = await getDoneIds();
            const doneSet = new Set(doneIds);
            questions = all.filter((q) => !doneSet.has(q.id));

            if (questions.length === 0) {
                showToast(all.length > 0 ? "该条件下所有题目都已做完" : "没有找到符合条件的题目", "error");
                $("startBtn").disabled = false;
                return;
            }

            currentIdx = 0;
            score = { correct: 0, wrong: 0 };
            answered = false;
            selectedAnswer = null;

            $("emptyState").style.display = "none";
            $("completePage").style.display = "none";
            $("scoreBar").style.display = "grid";
            $("quizCard").style.display = "block";
            $("startBtn").disabled = false;

            updateStatsSummary();
            renderQuestion();
        } catch (e) {
            showToast("加载题目失败: " + e.message, "error");
            $("startBtn").disabled = false;
        }
    };

    window.retryWrong = async function () {
        const wrongList = await getWrongList();
        if (wrongList.length === 0) { showToast("错题集为空", "error"); return; }
        switchTab("quiz");
        questions = wrongList.map(w => ({
            id: w.id, text: w.text,
            option_a: w.option_a, option_b: w.option_b,
            option_c: w.option_c, option_d: w.option_d,
            layer: w.layer, difficulty: w.difficulty
        }));
        questions.sort(() => Math.random() - 0.5);
        currentIdx = 0;
        score = { correct: 0, wrong: 0 };
        answered = false; selectedAnswer = null;
        $("emptyState").style.display = "none";
        $("completePage").style.display = "none";
        $("scoreBar").style.display = "grid";
        $("quizCard").style.display = "block";
        renderQuestion();
        showToast(`开始重做 ${questions.length} 道错题`, "success");
    };

    function renderQuestion() {
        const q = questions[currentIdx];
        $("questionProgress").textContent = `第 ${currentIdx + 1} 题 / 共 ${questions.length} 题`;
        $("questionText").textContent = q.text;

        const options = ["A", "B", "C", "D"];
        const optionTexts = { A: q.option_a, B: q.option_b, C: q.option_c, D: q.option_d };
        let html = "";
        for (const letter of options) {
            html += `
                <button class="option-btn" data-letter="${letter}" onclick="selectOption('${letter}')">
                    <span class="option-letter">${letter}</span>
                    <span>${optionTexts[letter]}</span>
                </button>`;
        }
        $("optionsList").innerHTML = html;
        $("submitBtn").style.display = "inline-flex";
        $("submitBtn").disabled = true;
        $("nextBtn").style.display = "none";
        $("explanationBox").className = "quiz-explanation";
        selectedAnswer = null;
        answered = false;
        updateScoreDisplay();
    }

    window.selectOption = function (letter) {
        if (answered) return;
        selectedAnswer = letter;
        document.querySelectorAll(".option-btn").forEach(btn => {
            btn.classList.toggle("selected", btn.dataset.letter === letter);
        });
        $("submitBtn").disabled = false;
    };

    window.submitAnswer = async function () {
        if (!selectedAnswer || answered) return;
        answered = true;
        const q = questions[currentIdx];

        try {
            const sessionId = localStorage.getItem('graphrag_session_id') || 'default';
            const body = { question_id: q.id, selected_answer: selectedAnswer, session_id: sessionId };
            const result = await apiPost("/api/quiz/answer", body);

            document.querySelectorAll(".option-btn").forEach(btn => {
                const letter = btn.dataset.letter;
                btn.classList.add("disabled");
                if (letter === result.correct_answer) btn.classList.add("correct");
                if (letter === selectedAnswer && !result.correct) btn.classList.add("wrong");
            });

            await markDone(q.id);

            if (!result.correct) {
                score.wrong++;
                await addWrong(q, selectedAnswer, result);
            } else {
                score.correct++;
                await removeWrong(q.id);
            }
            updateScoreDisplay();

            const box = $("explanationBox");
            box.className = "quiz-explanation " + (result.correct ? "correct-bg" : "wrong-bg");
            box.innerHTML = `
                <div class="explanation-label ${result.correct ? "explanation-correct" : "explanation-wrong"}">
                    ${result.correct ? "正确" : "错误，正确答案: " + result.correct_answer}
                </div>
                <div>${result.explanation}</div>`;

            $("submitBtn").style.display = "none";
            $("nextBtn").style.display = "inline-flex";
            $("nextBtn").textContent = currentIdx < questions.length - 1 ? "下一题" : "查看结果";
        } catch (e) {
            showToast("提交失败: " + e.message, "error");
            answered = false;
        }
    };

    window.nextQuestion = function () {
        currentIdx++;
        if (currentIdx >= questions.length) showComplete();
        else renderQuestion();
    };

    function showComplete() {
        $("quizCard").style.display = "none";
        $("completePage").style.display = "block";
        $("finalCorrect").textContent = score.correct;
        $("finalTotal").textContent = questions.length;
        const pct = questions.length > 0 ? Math.round((score.correct / questions.length) * 100) : 0;
        $("finalAccuracy").textContent = `正确率 ${pct}%`;
        updateStatsSummary();
    }

    function resetQuizState() {
        questions = [];
        currentIdx = 0;
        score = { correct: 0, wrong: 0 };
        $("quizCard").style.display = "none";
        $("completePage").style.display = "none";
        $("scoreBar").style.display = "none";
        $("emptyState").style.display = "block";
        updateStatsSummary();
    }

    window.resetQuiz = resetQuizState;

    function updateScoreDisplay() {
        const total = score.correct + score.wrong;
        $("progressText").textContent = `${total}/${questions.length}`;
        $("correctCount").textContent = score.correct;
        $("wrongCount").textContent = score.wrong;
        const pct = total > 0 ? Math.round((score.correct / total) * 100) : 0;
        $("accuracy").textContent = pct + "%";
    }

    async function updateStatsSummary() {
        const doneIds = await getDoneIds();
        const wrongList = await getWrongList();
        $("doneCount").textContent = doneIds.length;
        const wc = wrongList.length;
        const sb = $("wrongBadgeSidebar");
        if (sb) { sb.textContent = wc; sb.style.display = wc > 0 ? "inline" : "none"; }
        const tb = $("wrongBadgeTab");
        if (tb) { tb.textContent = wc; tb.style.display = wc > 0 ? "inline-flex" : "none"; }
    }

    // ==================== 错题集 ====================

    async function renderWrongList() {
        const list = await getWrongList();
        const container = $("wrongListContainer");
        if (list.length === 0) {
            container.innerHTML = `<div class="quiz-empty">
                <div class="empty-icon"><i class="fas fa-check-circle"></i></div>
                <p>错题集为空，继续保持！</p></div>`;
            $("retryAllBtn").style.display = "none";
            $("clearWrongBtn").style.display = "none";
            return;
        }
        $("retryAllBtn").style.display = "inline-flex";
        $("clearWrongBtn").style.display = "inline-flex";
        const diffLabel = { basic: "基础", medium: "中等", hard: "困难" };
        let html = "";
        for (const w of list) {
            html += `
                <div class="wrong-item" data-id="${w.id}">
                    <div class="wrong-item-header" onclick="toggleWrongDetail('${w.id}')">
                        <div class="wrong-item-info">
                            <span class="wrong-tag ${w.difficulty}">${diffLabel[w.difficulty] || w.difficulty}</span>
                            <span class="wrong-tag layer">${w.layer}</span>
                            <span class="wrong-item-text">${w.text}</span>
                        </div>
                        <i class="fas fa-chevron-down wrong-arrow" id="arrow_${w.id}"></i>
                    </div>
                    <div class="wrong-detail" id="detail_${w.id}">
                        <div class="wrong-detail-options">${
                            ["A","B","C","D"].map(l => {
                                const isCorrect = l === w.correct_answer;
                                const isSelected = l === w.selected;
                                let cls = "wrong-opt";
                                if (isCorrect) cls += " correct-opt";
                                if (isSelected && !isCorrect) cls += " wrong-opt-selected";
                                const mark = isCorrect
                                    ? '<i class="fas fa-check" style="color:var(--green);margin-left:auto;font-size:10px;"></i>'
                                    : (isSelected ? '<i class="fas fa-xmark" style="color:var(--red);margin-left:auto;font-size:10px;"></i>' : '');
                                return `<div class="${cls}">
                                    <span class="wrong-opt-letter">${l}</span>
                                    <span>${w["option_" + l.toLowerCase()]}</span>${mark}</div>`;
                            }).join("")
                        }</div>
                        <div class="wrong-explanation">
                            <div class="explanation-label explanation-wrong">解析</div>
                            <div>${w.explanation}</div>
                        </div>
                        <button class="btn-remove-wrong" onclick="removeWrongItem('${w.id}')">
                            <i class="fas fa-trash-can"></i> 移出错题集
                        </button>
                    </div>
                </div>`;
        }
        container.innerHTML = html;
    }

    window.toggleWrongDetail = function (id) {
        const detail = $("detail_" + id);
        const arrow = $("arrow_" + id);
        const isOpen = detail.classList.contains("open");
        document.querySelectorAll(".wrong-detail.open").forEach(d => d.classList.remove("open"));
        document.querySelectorAll(".wrong-arrow.open").forEach(a => a.classList.remove("open"));
        if (!isOpen) { detail.classList.add("open"); arrow.classList.add("open"); }
    };

    window.removeWrongItem = async function (id) {
        await removeWrong(id);
        await renderWrongList();
        await updateStatsSummary();
        showToast("已从错题集移除", "success");
    };

    window.clearWrongList = async function () {
        if (!confirm("确定要清空错题集吗？")) return;
        _wrongCache = null;
        await apiDelete("/api/quiz/wrong");
        await renderWrongList();
        await updateStatsSummary();
        showToast("错题集已清空", "success");
    };

    // ==================== 表单 ====================

    window.toggleForm = function () {
        const active = $("addForm").classList.toggle("active");
        const toggleBtn = document.querySelector(".form-toggle");
        const arrow = $("formToggleArrow");
        if (toggleBtn) toggleBtn.classList.toggle("open", active);
        if (arrow) arrow.style.transform = active ? "rotate(180deg)" : "";
    };

    window.addQuestion = async function () {
        const data = {
            text: $("fText").value.trim(),
            option_a: $("fA").value.trim(),
            option_b: $("fB").value.trim(),
            option_c: $("fC").value.trim(),
            option_d: $("fD").value.trim(),
            correct_answer: $("fAnswer").value,
            explanation: $("fExplanation").value.trim(),
            difficulty: $("fDiff").value,
            layer: $("fLayer").value,
            chapter: $("fChapter").value.trim()
        };
        for (const k of ["text", "option_a", "option_b", "option_c", "option_d", "explanation"]) {
            if (!data[k]) { showToast("请填写所有必填字段", "error"); return; }
        }
        try {
            await apiPost("/api/quiz/questions", data);
            showToast("题目添加成功！", "success");
            ["fText","fA","fB","fC","fD","fExplanation","fChapter"].forEach(id => {
                const el = $(id);
                if (el) el.value = "";
            });
            loadChapterStructure();
        } catch (e) { showToast("添加失败: " + e.message, "error"); }
    };

    // ==================== AI 生成题目 ====================

    let aiGenerated = [];

    window.toggleAiForm = function () {
        const active = $("aiForm").classList.toggle("active");
        const arrow = $("aiFormToggleArrow");
        if (arrow) arrow.style.transform = active ? "rotate(180deg)" : "";
    };

    window.generateAiQuestions = async function () {
        const count = Math.min(10, Math.max(1, parseInt($("aiCount").value, 10) || 5));
        $("aiCount").value = count;
        const seeds = $("aiSeeds").value.trim()
            ? $("aiSeeds").value.split(/[,，]/).map(s => s.trim()).filter(Boolean)
            : null;
        const btn = $("aiGenerateBtn");
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 生成中...';
        try {
            const data = await apiPost("/api/quiz/generate", {
                layer: $("aiLayer").value || null,
                difficulty: $("aiDiff").value || null,
                count: count,
                seed_entities: seeds,
            });
            aiGenerated = data.questions || [];
            renderAiCards();
            showToast(`生成 ${aiGenerated.length} 道题（种子：${(data.seed_entities || []).join("、") || "随机"}），请预览确认后入库`,
                "success");
        } catch (e) {
            showToast("生成失败: " + e.message, "error");
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> 生成题目';
        }
    };

    function renderAiCards() {
        const wrap = $("aiPreview");
        wrap.innerHTML = "";
        aiGenerated.forEach((q, idx) => {
            if (!q) return;
            const card = document.createElement("div");
            card.className = "ai-q-card";
            card.id = "aiQCard" + idx;
            const refs = (q.references || []).map(r => `<span class="ai-q-ref">${escapeHtml(r)}</span>`).join("");
            card.innerHTML = `
                <div class="ai-q-head">
                    <span class="ai-q-badge">${escapeHtml(q.layer)}</span>
                    <span class="ai-q-badge">${escapeHtml(q.difficulty)}</span>
                    ${q.chapter ? `<span class="ai-q-badge">${escapeHtml(q.chapter)}</span>` : ""}
                </div>
                <textarea class="ai-q-text" rows="2">${escapeHtml(q.text)}</textarea>
                <div class="form-row">
                    <input class="ai-q-opt" value="${escapeHtml(q.option_a)}" placeholder="选项 A">
                    <input class="ai-q-opt" value="${escapeHtml(q.option_b)}" placeholder="选项 B">
                </div>
                <div class="form-row">
                    <input class="ai-q-opt" value="${escapeHtml(q.option_c)}" placeholder="选项 C">
                    <input class="ai-q-opt" value="${escapeHtml(q.option_d)}" placeholder="选项 D">
                </div>
                <div class="ai-q-meta">
                    <label>正确答案</label>
                    <select class="ai-q-answer">
                        ${["A", "B", "C", "D"].map(l =>
                            `<option value="${l}" ${l === q.correct_answer ? "selected" : ""}>${l}</option>`).join("")}
                    </select>
                </div>
                <textarea class="ai-q-expl" rows="2">${escapeHtml(q.explanation)}</textarea>
                ${refs ? `<div class="ai-q-refs">关联实体：${refs}</div>` : ""}
                <div class="ai-q-actions">
                    <button class="btn-ai-save" onclick="confirmAiQuestion(${idx})">
                        <i class="fas fa-check"></i> 确认入库
                    </button>
                    <button class="btn-ai-discard" onclick="discardAiQuestion(${idx})">
                        <i class="fas fa-trash"></i> 丢弃
                    </button>
                </div>`;
            wrap.appendChild(card);
        });
    }

    window.confirmAiQuestion = async function (idx) {
        const card = $("aiQCard" + idx);
        const q = aiGenerated[idx];
        if (!card || !q) return;
        const opts = Array.from(card.querySelectorAll(".ai-q-opt")).map(i => i.value.trim());
        const payload = {
            text: card.querySelector(".ai-q-text").value.trim(),
            option_a: opts[0], option_b: opts[1], option_c: opts[2], option_d: opts[3],
            correct_answer: card.querySelector(".ai-q-answer").value,
            explanation: card.querySelector(".ai-q-expl").value.trim(),
            difficulty: q.difficulty,
            layer: q.layer,
            chapter: q.chapter || "",
            references: q.references || [],
            source: "ai",
        };
        for (const k of ["text", "option_a", "option_b", "option_c", "option_d", "explanation"]) {
            if (!payload[k]) { showToast("请填写所有必填字段", "error"); return; }
        }
        try {
            await apiPost("/api/quiz/questions", payload);
            card.classList.add("saved");
            card.querySelectorAll("textarea,input,select,button").forEach(el => el.disabled = true);
            showToast("题目已入库", "success");
            loadChapterStructure();
        } catch (e) {
            showToast("入库失败: " + e.message, "error");
        }
    };

    window.discardAiQuestion = function (idx) {
        const card = $("aiQCard" + idx);
        if (card) card.remove();
        aiGenerated[idx] = null;
    };

    // ==================== Toast ====================

    function showToast(msg, type) {
        const toast = $("toast");
        toast.textContent = msg;
        toast.className = "quiz-toast " + type + " show";
        setTimeout(() => { toast.className = "quiz-toast"; }, 3000);
    }

    // ==================== 试卷考试 ====================

    let examState = {
        paperId: null,
        questions: [],
        currentIdx: 0,
        answers: {},
    };

    window.switchTab = function (tab) {
        currentTab = tab;
        document.querySelectorAll(".quiz-tab").forEach(function (t) {
            t.classList.toggle("active", t.dataset.tab === tab);
        });
        $("quizPanel").style.display = tab === "quiz" ? "block" : "none";
        $("wrongPanel").style.display = tab === "wrong" ? "block" : "none";
        $("examPanel").style.display = tab === "exam" ? "block" : "none";
        if (tab === "wrong") renderWrongList();
        if (tab === "exam") loadExamPapers();
        // 隐藏 exam 子视图
        if (tab !== "exam") {
            $("examTakeView").style.display = "none";
            $("examResultView").style.display = "none";
            $("examListView").style.display = "block";
        }
    };

    // --- Exam Paper List ---

    async function loadExamPapers() {
        $("examListView").style.display = "block";
        $("examTakeView").style.display = "none";
        $("examResultView").style.display = "none";
        try {
            var data = await apiGet("/api/exam/papers");
            renderExamPapers(data);
            loadExamHistory();
        } catch (e) {
            $("examPaperList").innerHTML = '<div class="exam-empty"><i class="fas fa-exclamation-circle"></i><p>加载试卷失败: ' + e.message + '</p></div>';
        }
    }

    function renderExamPapers(data) {
        var years = data.papers_by_year || {};
        var yearKeys = Object.keys(years).sort().reverse();
        if (yearKeys.length === 0) {
            $("examPaperList").innerHTML = '<div class="exam-empty"><i class="fas fa-file-alt"></i><p>暂无试卷，请先导入</p></div>';
            return;
        }
        var html = "";
        for (var i = 0; i < yearKeys.length; i++) {
            var yr = yearKeys[i];
            var papers = years[yr];
            html += '<div class="exam-year-group"><div class="exam-year-header"><i class="fas fa-calendar-alt"></i> ' + yr + ' 年</div><div class="exam-paper-list">';
            for (var j = 0; j < papers.length; j++) {
                var p = papers[j];
                html += '<div class="exam-paper-card" onclick="startExam(\'' + p.id + '\')">' +
                    '<div class="paper-icon"><i class="fas fa-file-alt"></i></div>' +
                    '<div class="paper-body">' +
                        '<div class="paper-title">' + p.title + '</div>' +
                        '<div class="paper-meta">' +
                            '<span><i class="fas fa-question-circle"></i> ' + p.question_count + ' 题</span>' +
                            '<span><i class="fas fa-star"></i> ' + p.total_score + ' 分</span>' +
                            (p.source ? '<span><i class="fas fa-building"></i> ' + p.source + '</span>' : '') +
                        '</div>' +
                        (p.description ? '<div class="paper-desc">' + p.description + '</div>' : '') +
                    '</div>' +
                    '<div class="paper-arrow"><i class="fas fa-chevron-right"></i></div>' +
                '</div>';
            }
            html += '</div></div>';
        }
        $("examPaperList").innerHTML = html;
    }

    async function loadExamHistory() {
        var sid = localStorage.getItem("graphrag_session_id") || "default";
        try {
            var history = await apiGet("/api/exam/history/" + encodeURIComponent(sid));
            if (history && history.length > 0) {
                $("examHistorySection").style.display = "block";
                renderExamHistory(history);
            }
        } catch (e) {
            // 静默失败
        }
    }

    function renderExamHistory(history) {
        var html = "";
        for (var i = 0; i < history.length; i++) {
            var h = history[i];
            var accClass = h.accuracy >= 80 ? "high" : (h.accuracy >= 60 ? "medium" : "low");
            html += '<div class="exam-history-item" onclick="viewExamResult(' + h.result_id + ')">' +
                '<div class="history-score">' + h.total_score + '/' + h.max_score + '</div>' +
                '<div class="history-body">' +
                    '<div class="history-title">' + h.paper_title + '</div>' +
                    '<div class="history-meta">' +
                        new Date(h.completed_at).toLocaleDateString("zh-CN") +
                        ' · 正确 ' + h.correct_count + ' · 错误 ' + h.wrong_count +
                    '</div>' +
                '</div>' +
                '<span class="history-accuracy ' + accClass + '">' + h.accuracy + '%</span>' +
            '</div>';
        }
        $("examHistoryList").innerHTML = html;
    }

    // --- Exam Taking ---

    window.startExam = async function (paperId) {
        try {
            var paper = await apiGet("/api/exam/papers/" + encodeURIComponent(paperId));
            examState.paperId = paperId;
            examState.questions = paper.questions || [];
            examState.currentIdx = 0;
            examState.answers = {};
            examState._totalScore = paper.total_score || 0;

            $("examListView").style.display = "none";
            $("examTakeView").style.display = "flex";
            $("examResultView").style.display = "none";
            $("examTakeTitle").textContent = paper.title;
            $("examQuestionNav").style.display = "none";

            updateExamHeader();
            renderExamQuestion();
            renderQuestionNav();
        } catch (e) {
            showToast("加载试卷失败: " + e.message, "error");
        }
    };

    function updateExamHeader() {
        var total = examState.questions.length;
        var answered = Object.keys(examState.answers).length;
        $("examAnsweredBadge").textContent = "已答 " + answered + "/" + total;
        var cumScore = 0;
        for (var id in examState.answers) {
            for (var i = 0; i < examState.questions.length; i++) {
                if (examState.questions[i].id === id) {
                    cumScore += examState.questions[i].score || 0;
                    break;
                }
            }
        }
        $("examScoreProgress").textContent = cumScore + "/" + (examState._totalScore || 0) + " 分";
    }

    function renderQuestionNav() {
        var total = examState.questions.length;
        var html = "";
        for (var i = 0; i < total; i++) {
            var qid = examState.questions[i].id;
            var cls = "exam-nav-btn";
            if (i === examState.currentIdx) cls += " current";
            else if (examState.answers[qid]) cls += " answered";
            html += '<button class="' + cls + '" onclick="jumpToQuestion(' + i + ')">' + (i + 1) + '</button>';
        }
        $("examNavGrid").innerHTML = html;
    }

    window.toggleQuestionNav = function () {
        var nav = $("examQuestionNav");
        nav.style.display = nav.style.display === "none" ? "block" : "none";
        if (nav.style.display !== "none") renderQuestionNav();
    };

    window.jumpToQuestion = function (idx) {
        examState.currentIdx = idx;
        renderExamQuestion();
        renderQuestionNav();
        $("examQuestionNav").style.display = "none";
    };

    function renderExamQuestion() {
        var q = examState.questions[examState.currentIdx];
        var total = examState.questions.length;
        $("examQuestionProgress").textContent = "第 " + (examState.currentIdx + 1) + " 题 / 共 " + total + " 题";
        var score = q.score || 0;
        var diffLabel = { basic: "基础", medium: "中等", hard: "困难" };
        var meta = [];
        if (q.layer) meta.push(q.layer);
        if (q.difficulty) meta.push(diffLabel[q.difficulty] || q.difficulty);
        $("examQuestionScore").textContent = score + " 分" + (meta.length ? " · " + meta.join(" · ") : "");

        $("examQuestionText").textContent = q.text;

        var options = ["A", "B", "C", "D"];
        var optionTexts = { A: q.option_a, B: q.option_b, C: q.option_c, D: q.option_d };
        var existingAnswer = examState.answers[q.id];
        var html = "";
        for (var i = 0; i < options.length; i++) {
            var letter = options[i];
            var selected = existingAnswer === letter ? " selected" : "";
            html += '<button class="option-btn' + selected + '" data-letter="' + letter + '" onclick="selectExamOption(\'' + letter + '\')">' +
                '<span class="option-letter">' + letter + '</span>' +
                '<span>' + (optionTexts[letter] || "") + '</span>' +
                '</button>';
        }
        $("examOptionsList").innerHTML = html;

        $("examPrevBtn").style.visibility = examState.currentIdx > 0 ? "visible" : "hidden";
        var nextBtn = $("examNextBtn");
        if (examState.currentIdx < total - 1) {
            nextBtn.innerHTML = '下一题 <i class="fas fa-chevron-right"></i>';
        } else {
            nextBtn.innerHTML = '交卷 <i class="fas fa-check"></i>';
        }

        updateExamHeader();
    }

    window.selectExamOption = function (letter) {
        var q = examState.questions[examState.currentIdx];
        examState.answers[q.id] = letter;
        document.querySelectorAll("#examOptionsList .option-btn").forEach(function (btn) {
            btn.classList.toggle("selected", btn.dataset.letter === letter);
        });
    };

    window.examNextQuestion = function () {
        var total = examState.questions.length;
        if (examState.currentIdx >= total - 1) {
            // 确认交卷
            var unanswered = 0;
            for (var i = 0; i < examState.questions.length; i++) {
                if (!examState.answers[examState.questions[i].id]) unanswered++;
            }
            var msg = "确定要提交试卷吗？";
            if (unanswered > 0) msg += "\n还有 " + unanswered + " 道题未作答。";
            if (!confirm(msg)) return;
            submitExam();
            return;
        }
        examState.currentIdx++;
        renderExamQuestion();
    };

    window.examPrevQuestion = function () {
        if (examState.currentIdx > 0) {
            examState.currentIdx--;
            renderExamQuestion();
        }
    };

    window.submitExam = async function () {
        var sid = localStorage.getItem("graphrag_session_id") || "default";
        try {
            $("examSubmitBtn").disabled = true;
            $("examSubmitBtn").textContent = "批改中...";
            var result = await apiPost("/api/exam/submit", {
                session_id: sid,
                paper_id: examState.paperId,
                answers: examState.answers
            });
            $("examTakeView").style.display = "none";
            $("examResultView").style.display = "block";
            renderExamResult(result);
        } catch (e) {
            showToast("提交失败: " + e.message, "error");
            $("examSubmitBtn").disabled = false;
            $("examSubmitBtn").innerHTML = '<i class="fas fa-check"></i> 交卷';
        }
    };

    // --- Exam Results ---

    function renderExamResult(result) {
        console.log("[exam] renderExamResult called", {total: result.total_score, detailsCount: (result.details || []).length});
        try {
        var total = result.total_score || 0;
        var max = result.max_score || 0;
        var accuracy = result.accuracy || 0;
        var accClass = accuracy >= 80 ? "high" : (accuracy >= 60 ? "medium" : "low");

        // Score ring
        var circumference = 2 * Math.PI * 52;
        var dashOffset = circumference * (1 - accuracy / 100);
        $("examResultScore").innerHTML =
            '<div class="exam-score-ring-wrap">' +
            '<div class="exam-score-ring">' +
                '<svg width="140" height="140" viewBox="0 0 140 140">' +
                    '<circle class="ring-bg" cx="70" cy="70" r="52"/>' +
                    '<circle class="ring-fill ' + accClass + '" cx="70" cy="70" r="52" ' +
                        'stroke-dasharray="' + circumference + '" stroke-dashoffset="' + circumference + '" ' +
                        'id="scoreRingFill"/>' +
                '</svg>' +
                '<div class="ring-center">' +
                    '<div class="ring-score">' + total + '</div>' +
                    '<div class="ring-total">/ ' + max + '</div>' +
                    '<div class="ring-pct ' + accClass + '">' + accuracy + '%</div>' +
                '</div>' +
            '</div></div>' +
            '<div class="exam-result-meta">' +
                '<div class="meta-item"><span class="meta-value green">' + (result.correct_count || 0) + '</span>正确</div>' +
                '<div class="meta-item"><span class="meta-value red">' + (result.wrong_count || 0) + '</span>错误</div>' +
            '</div>';

        // Animate ring
        setTimeout(function () {
            var ring = document.getElementById("scoreRingFill");
            if (ring) ring.style.strokeDashoffset = dashOffset;
        }, 100);

        // LLM 分析
        if (result.analysis && result.analysis.summary) {
            $("examAnalysisCard").style.display = "block";
            var analysis = result.analysis;
            var tagsHtml = "";
            if (analysis.weak_areas) {
                for (var i = 0; i < analysis.weak_areas.length; i++) {
                    var wa = analysis.weak_areas[i];
                    tagsHtml += '<span class="weak-area-tag">' + wa.value + '</span>';
                }
            }
            $("examAnalysisCard").innerHTML =
                '<div class="analysis-header"><i class="fas fa-robot"></i> AI 薄弱点分析</div>' +
                '<div class="analysis-summary">' + analysis.summary + '</div>' +
                (tagsHtml ? '<div class="weak-area-tags">' + tagsHtml + '</div>' : '') +
                (analysis.study_advice ? '<div class="study-advice"><strong>学习建议：</strong>' + analysis.study_advice + '</div>' : '');
        } else {
            $("examAnalysisCard").style.display = "none";
        }

        // 错误统计
        if (result.error_stats) {
            renderExamErrorStats(result.error_stats);
        } else {
            $("examErrorStats").style.display = "none";
        }

        // 答题详情（可折叠）
        renderExamResultDetails(result);

        examState._lastResultId = result.result_id;
        } catch (renderErr) {
            console.error("[exam] renderExamResult error:", renderErr);
        }
    }

    function renderExamErrorStats(stats) {
        $("examErrorStats").style.display = "block";
        var html = '<div class="stats-title"><i class="fas fa-chart-bar"></i> 错误分布统计</div>';

        html += renderErrorChartGroup("按层次", stats.by_layer || {});
        html += renderErrorChartGroup("按难度", stats.by_difficulty || {});
        html += renderErrorChartGroup("按章节", stats.by_chapter || {});

        $("examErrorStats").innerHTML = html;
    }

    function renderErrorChartGroup(label, data) {
        var keys = Object.keys(data);
        if (keys.length === 0) return "";
        var maxVal = 1;
        for (var i = 0; i < keys.length; i++) {
            maxVal = Math.max(maxVal, data[keys[i]]);
        }
        var diffLabels = { basic: "基础", medium: "中等", hard: "困难" };
        var html = '<div class="exam-error-chart-group">';
        html += '<div class="exam-error-chart-label">' + label + '</div>';
        html += '<div class="exam-error-chart-bars">';
        for (var i = 0; i < keys.length; i++) {
            var k = keys[i];
            var v = data[k];
            var pct = Math.round(v / maxVal * 100);
            var displayName = diffLabels[k] || k;
            html += '<div class="exam-error-bar-row">' +
                '<span class="exam-error-bar-name">' + displayName + '</span>' +
                '<div class="exam-error-bar-wrap"><div class="exam-error-bar-fill" style="width:' + pct + '%"></div></div>' +
                '<span class="exam-error-bar-count">' + v + '</span>' +
            '</div>';
        }
        html += '</div></div>';
        return html;
    }

    function escapeHtml(str) {
        if (!str) return "";
        return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    window._allDetailsExpanded = false;

    window.toggleAllDetails = function () {
        var bodies = document.querySelectorAll(".exam-detail-body");
        var toggles = document.querySelectorAll(".exam-detail-toggle");
        window._allDetailsExpanded = !window._allDetailsExpanded;
        var btn = document.getElementById("toggleAllDetailsBtn");
        if (window._allDetailsExpanded) {
            bodies.forEach(function (el) { el.classList.add("open"); });
            toggles.forEach(function (el) { el.classList.add("open"); });
            if (btn) btn.innerHTML = '<i class="fas fa-compress"></i> 收起全部';
        } else {
            bodies.forEach(function (el) { el.classList.remove("open"); });
            toggles.forEach(function (el) { el.classList.remove("open"); });
            if (btn) btn.innerHTML = '<i class="fas fa-expand"></i> 展开全部';
        }
    };

    function renderExamResultDetails(result) {
        var details = result.details || [];
        console.log("[exam] renderExamResultDetails called, details.length=" + details.length);
        var wrongCount = 0;
        for (var i = 0; i < details.length; i++) {
            if (!details[i].correct) wrongCount++;
        }
        var container = $("examResultDetails");
        if (!container) {
            console.error("[exam] examResultDetails element not found in DOM!");
            return;
        }
        var html = '<div style="margin-top:20px;padding-top:16px;border-top:1px solid var(--border-1);">';
        html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">';
        html += '<div class="stats-title" style="font-size:15px;"><i class="fas fa-list-check"></i> 答题详情 <span style="font-weight:400;color:var(--text-3);font-size:12px;">（共' + details.length + '题，错' + wrongCount + '题 · 点击题目展开解析）</span></div>';
        html += '<button id="toggleAllDetailsBtn" class="btn-toggle-all-details" onclick="toggleAllDetails()"><i class="fas fa-expand"></i> 展开全部</button>';
        html += '</div>';
        for (var i = 0; i < details.length; i++) {
            var d = details[i];
            var itemCls = d.correct ? "correct-detail" : "wrong-detail";
            var statusCls = d.correct ? "correct" : "wrong";
            var statusText = d.correct ? "✓ 正确" : "✗ 错误";
            var qText = d.text || ("第 " + (i + 1) + " 题");
            var toggleOpen = d.correct ? "" : " open";
            var bodyOpen = d.correct ? "" : " open";

            html += '<div class="exam-detail-item ' + itemCls + '">' +
                '<button class="exam-detail-toggle' + toggleOpen + '" onclick="toggleExamDetail(this, ' + i + ')" style="cursor:pointer;">' +
                    '<span class="detail-status ' + statusCls + '">' + statusText + '</span>' +
                    '<span class="detail-q-text">' + escapeHtml(qText) + '</span>' +
                    '<span class="detail-score-tag">' + (d.score || 0) + '分</span>' +
                    '<i class="fas fa-chevron-down detail-arrow"></i>' +
                '</button>' +
                '<div class="exam-detail-body' + bodyOpen + '" id="examDetailBody' + i + '">' +
                    (d.correct ? '' : '<div class="detail-label">你的答案：<span style="color:var(--red);">' + escapeHtml(d.selected_answer || examState.answers[d.question_id] || "未作答") + '</span></div>') +
                    '<div class="detail-label">正确答案：<span style="color:var(--green);">' + escapeHtml(d.correct_answer || "") + '</span></div>' +
                    (d.explanation ? '<div class="detail-label">解析</div><div>' + escapeHtml(d.explanation) + '</div>' : '') +
                '</div>' +
            '</div>';
        }
        html += '</div>';
        container.innerHTML = html;
        console.log("[exam] renderExamResultDetails done, html length=" + html.length);
    }

    window.toggleExamDetail = function (btn, idx) {
        var body = document.getElementById("examDetailBody" + idx);
        var isOpen = body.classList.contains("open");
        // Close all
        document.querySelectorAll(".exam-detail-body.open").forEach(function (el) { el.classList.remove("open"); });
        document.querySelectorAll(".exam-detail-toggle.open").forEach(function (el) { el.classList.remove("open"); });
        if (!isOpen) {
            body.classList.add("open");
            btn.classList.add("open");
        }
    };

    // --- Targeted Practice ---

    window.startTargetedPractice = async function () {
        var sid = localStorage.getItem("graphrag_session_id") || "default";
        try {
            showToast("正在生成针对性练习题...", "success");
            var data = await apiGet("/api/exam/recommendations/" + encodeURIComponent(sid) + "?limit=20");
            if (!data.questions || data.questions.length === 0) {
                showToast("暂无推荐题目", "error");
                return;
            }
            questions = data.questions;
            currentIdx = 0;
            score = { correct: 0, wrong: 0 };
            answered = false;
            selectedAnswer = null;
            switchTab("quiz");
            $("emptyState").style.display = "none";
            $("completePage").style.display = "none";
            $("scoreBar").style.display = "grid";
            $("quizCard").style.display = "block";
            updateStatsSummary();
            renderQuestion();
            showToast("已加载 " + data.questions.length + " 道针对性练习题", "success");
        } catch (e) {
            showToast("加载推荐失败: " + e.message, "error");
        }
    };

    window.viewExamResult = async function (resultId) {
        try {
            var result = await apiGet("/api/exam/result/" + resultId);
            $("examListView").style.display = "none";
            $("examTakeView").style.display = "none";
            $("examResultView").style.display = "block";
            renderExamResult(result);
        } catch (e) {
            showToast("加载考试记录失败: " + e.message, "error");
        }
    };

    window.backToExamList = function () {
        $("examTakeView").style.display = "none";
        $("examResultView").style.display = "none";
        $("examListView").style.display = "block";
        // 重新加载试卷列表和历史
        loadExamPapers();
    };

    // ==================== 键盘快捷键（支持刷题 + 考试模式）====================

    document.addEventListener("keydown", function (e) {
        if (currentTab === "exam" && $("examTakeView").style.display !== "none") {
            var key = e.key.toUpperCase();
            if (["A", "B", "C", "D"].includes(key)) {
                selectExamOption(key);
                return;
            }
            if (e.key === "ArrowRight") {
                examNextQuestion();
                return;
            }
            if (e.key === "ArrowLeft") {
                examPrevQuestion();
                return;
            }
        }
        if (currentTab === "quiz" && questions.length) {
            var key = e.key.toUpperCase();
            if (["A","B","C","D"].includes(key)) { selectOption(key); }
            else if (e.key === "Enter") {
                if (answered) nextQuestion();
                else if (selectedAnswer) submitAnswer();
            }
        }
    });

    // 窗口大小变化时处理侧边栏状态
    window.addEventListener("resize", function () {
        const sidebar = $("quizSidebar");
        if (!isMobile() && sidebar.classList.contains("mobile-open")) {
            sidebar.classList.remove("mobile-open");
            $("sidebarOverlay").classList.remove("show");
        }
    });

    // ==================== 初始化 ====================

    // 恢复侧边栏折叠状态
    if (localStorage.getItem("quiz_sidebar_collapsed") === "1") {
        $("quizSidebar").classList.add("collapsed");
    }

    // 移动端初始状态
    if (isMobile()) {
        $("quizSidebar").classList.remove("collapsed");
    }

    loadChapterStructure();
    updateStatsSummary();
})();
