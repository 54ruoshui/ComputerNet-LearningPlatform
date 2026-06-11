/**
 * 刷题测验前端逻辑
 * - 已做题目不重复出题
 * - 错题自动收录错题集
 * - 数据持久化到服务端 JSON 文件
 */
(function () {
    "use strict";

    // --- 状态 ---
    let questions = [];
    let currentIdx = 0;
    let selectedAnswer = null;
    let answered = false;
    let score = { correct: 0, wrong: 0 };
    let currentTab = "quiz"; // "quiz" | "wrong"

    // 本地缓存（从服务端加载后缓存，减少请求）
    let _doneCache = null;
    let _wrongCache = null;

    // --- DOM 引用 ---
    const $ = (id) => document.getElementById(id);

    // ==================== API 调用 ====================

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
        try {
            const data = await apiGet("/api/quiz/done");
            _doneCache = data.ids || [];
        } catch {
            _doneCache = [];
        }
        return _doneCache;
    }

    async function markDone(id) {
        _doneCache = null; // invalidate
        return apiPost("/api/quiz/done/" + encodeURIComponent(id));
    }

    // ==================== Wrong List ====================

    async function getWrongList() {
        if (_wrongCache !== null) return _wrongCache;
        try {
            const data = await apiGet("/api/quiz/wrong");
            _wrongCache = data.list || [];
        } catch {
            _wrongCache = [];
        }
        return _wrongCache;
    }

    async function addWrong(q, selected, result) {
        _wrongCache = null; // invalidate
        const entry = {
            id: q.id,
            text: q.text,
            option_a: q.option_a,
            option_b: q.option_b,
            option_c: q.option_c,
            option_d: q.option_d,
            correct_answer: result.correct_answer,
            explanation: result.explanation,
            layer: q.layer,
            difficulty: q.difficulty,
            selected: selected,
            time: new Date().toISOString(),
        };
        return apiPost("/api/quiz/wrong", entry);
    }

    async function removeWrong(id) {
        _wrongCache = null; // invalidate
        return apiDelete("/api/quiz/wrong/" + encodeURIComponent(id));
    }

    // ==================== Tab 切换 ====================

    window.switchTab = function (tab) {
        currentTab = tab;
        document.querySelectorAll(".quiz-tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
        $("quizPanel").style.display = tab === "quiz" ? "block" : "none";
        $("wrongPanel").style.display = tab === "wrong" ? "block" : "none";
        if (tab === "wrong") renderWrongList();
    };

    // ==================== 刷题流程 ====================

    window.startQuiz = async function () {
        const layer = $("layerSelect").value;
        const diff = $("diffSelect").value;
        let url = "/api/quiz/questions?limit=50";
        if (layer) url += `&layer=${encodeURIComponent(layer)}`;
        if (diff) url += `&difficulty=${encodeURIComponent(diff)}`;

        try {
            $("startBtn").disabled = true;
            const data = await apiGet(url);
            const all = data.questions || [];

            // 过滤已做题目
            const doneIds = await getDoneIds();
            const doneSet = new Set(doneIds);
            questions = all.filter((q) => !doneSet.has(q.id));

            if (questions.length === 0) {
                if (all.length > 0) {
                    showToast("该筛选条件下所有题目都已做完", "error");
                } else {
                    showToast("没有找到符合条件的题目", "error");
                }
                $("startBtn").disabled = false;
                return;
            }

            // 重置状态
            currentIdx = 0;
            score = { correct: 0, wrong: 0 };
            answered = false;
            selectedAnswer = null;

            // 显示 UI
            $("emptyState").style.display = "none";
            $("completePage").style.display = "none";
            $("scoreBar").style.display = "flex";
            $("quizCard").style.display = "block";
            $("startBtn").disabled = false;

            updateStatsSummary();
            renderQuestion();
        } catch (e) {
            showToast("加载题目失败: " + e.message, "error");
            $("startBtn").disabled = false;
        }
    };

    /** 用错题集的题目重新做 */
    window.retryWrong = async function () {
        const wrongList = await getWrongList();
        if (wrongList.length === 0) {
            showToast("错题集为空", "error");
            return;
        }
        // 切到刷题 tab
        switchTab("quiz");

        questions = wrongList.map((w) => ({
            id: w.id,
            text: w.text,
            option_a: w.option_a,
            option_b: w.option_b,
            option_c: w.option_c,
            option_d: w.option_d,
            layer: w.layer,
            difficulty: w.difficulty,
        }));
        // 打乱顺序
        questions.sort(() => Math.random() - 0.5);

        currentIdx = 0;
        score = { correct: 0, wrong: 0 };
        answered = false;
        selectedAnswer = null;

        $("emptyState").style.display = "none";
        $("completePage").style.display = "none";
        $("scoreBar").style.display = "flex";
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

        $("submitBtn").style.display = "inline-block";
        $("submitBtn").disabled = true;
        $("nextBtn").style.display = "none";
        $("explanationBox").style.display = "none";
        selectedAnswer = null;
        answered = false;
        updateScoreDisplay();
    }

    window.selectOption = function (letter) {
        if (answered) return;
        selectedAnswer = letter;
        document.querySelectorAll(".option-btn").forEach((btn) => {
            btn.classList.toggle("selected", btn.dataset.letter === letter);
        });
        $("submitBtn").disabled = false;
    };

    window.submitAnswer = async function () {
        if (!selectedAnswer || answered) return;
        answered = true;
        const q = questions[currentIdx];

        try {
            const result = await apiPost("/api/quiz/answer", {
                question_id: q.id,
                selected_answer: selectedAnswer,
            });

            // 标记选项
            document.querySelectorAll(".option-btn").forEach((btn) => {
                const letter = btn.dataset.letter;
                btn.classList.add("disabled");
                if (letter === result.correct_answer) btn.classList.add("correct");
                if (letter === selectedAnswer && !result.correct) btn.classList.add("wrong");
            });

            // 记录已做
            await markDone(q.id);

            // 记录错题 / 移除已做对的错题
            if (!result.correct) {
                score.wrong++;
                await addWrong(q, selectedAnswer, result);
            } else {
                score.correct++;
                await removeWrong(q.id);
            }
            updateScoreDisplay();

            // 显示解析
            const box = $("explanationBox");
            box.className = "quiz-explanation " + (result.correct ? "correct-bg" : "wrong-bg");
            box.innerHTML = `
                <div class="explanation-label ${result.correct ? "explanation-correct" : "explanation-wrong"}">
                    ${result.correct ? "✓ 回答正确" : "✗ 回答错误，正确答案: " + result.correct_answer}
                </div>
                <div>${result.explanation}</div>`;
            box.style.display = "block";

            $("submitBtn").style.display = "none";
            $("nextBtn").style.display = "inline-block";
            $("nextBtn").textContent = currentIdx < questions.length - 1 ? "下一题" : "查看结果";
        } catch (e) {
            showToast("提交失败: " + e.message, "error");
            answered = false;
        }
    };

    window.nextQuestion = function () {
        currentIdx++;
        if (currentIdx >= questions.length) {
            showComplete();
        } else {
            renderQuestion();
        }
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

    window.resetQuiz = function () {
        questions = [];
        currentIdx = 0;
        score = { correct: 0, wrong: 0 };
        $("quizCard").style.display = "none";
        $("completePage").style.display = "none";
        $("scoreBar").style.display = "none";
        $("emptyState").style.display = "block";
        updateStatsSummary();
    };

    function updateScoreDisplay() {
        const total = score.correct + score.wrong;
        $("progressText").textContent = `${total}/${questions.length}`;
        $("correctCount").textContent = score.correct;
        $("wrongCount").textContent = score.wrong;
        const pct = total > 0 ? Math.round((score.correct / total) * 100) : 0;
        $("accuracy").textContent = pct + "%";
    }

    /** 更新统计摘要（已做/总数 + 错题数） */
    async function updateStatsSummary() {
        const doneIds = await getDoneIds();
        const wrongList = await getWrongList();
        $("doneCount").textContent = doneIds.length;
        const wc = wrongList.length;
        $("wrongBadge").textContent = wc;
        $("wrongBadge").style.display = wc > 0 ? "inline-flex" : "none";
        const tabBadge = $("wrongBadgeTab");
        if (tabBadge) {
            tabBadge.textContent = wc;
            tabBadge.style.display = wc > 0 ? "inline-flex" : "none";
        }
    }

    // ==================== 错题集 ====================

    async function renderWrongList() {
        const list = await getWrongList();
        const container = $("wrongListContainer");

        if (list.length === 0) {
            container.innerHTML = `
                <div class="quiz-empty">
                    <i class="fas fa-check-circle" style="font-size:2rem;color:var(--green);margin-bottom:12px;"></i>
                    <p>错题集为空，继续保持！</p>
                </div>`;
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
                        <div class="wrong-detail-options">
                            ${["A","B","C","D"].map((l) => {
                                const isCorrect = l === w.correct_answer;
                                const isSelected = l === w.selected;
                                let cls = "wrong-opt";
                                if (isCorrect) cls += " correct-opt";
                                if (isSelected && !isCorrect) cls += " wrong-opt-selected";
                                return `<div class="${cls}">
                                    <span class="wrong-opt-letter">${l}</span>
                                    <span>${w["option_" + l.toLowerCase()]}</span>
                                    ${isCorrect ? '<i class="fas fa-check" style="color:var(--green);margin-left:auto;"></i>' : ""}
                                    ${isSelected && !isCorrect ? '<i class="fas fa-xmark" style="color:var(--red);margin-left:auto;"></i>' : ""}
                                </div>`;
                            }).join("")}
                        </div>
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
        // 关闭其他
        document.querySelectorAll(".wrong-detail.open").forEach((d) => d.classList.remove("open"));
        document.querySelectorAll(".wrong-arrow.open").forEach((a) => a.classList.remove("open"));
        if (!isOpen) {
            detail.classList.add("open");
            arrow.classList.add("open");
        }
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

    // ==================== 录入表单 ====================

    window.toggleForm = function () {
        $("addForm").classList.toggle("active");
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
        };
        for (const key of ["text", "option_a", "option_b", "option_c", "option_d", "explanation"]) {
            if (!data[key]) { showToast("请填写所有必填字段", "error"); return; }
        }
        try {
            await apiPost("/api/quiz/questions", data);
            showToast("题目添加成功！", "success");
            $("fText").value = "";
            $("fA").value = "";
            $("fB").value = "";
            $("fC").value = "";
            $("fD").value = "";
            $("fExplanation").value = "";
        } catch (e) {
            showToast("添加失败: " + e.message, "error");
        }
    };

    // ==================== Toast ====================

    function showToast(msg, type) {
        const toast = $("toast");
        toast.textContent = msg;
        toast.className = "quiz-toast " + type + " show";
        setTimeout(() => { toast.className = "quiz-toast"; }, 3000);
    }

    // ==================== 键盘快捷键 ====================

    document.addEventListener("keydown", function (e) {
        if (currentTab !== "quiz" || !questions.length) return;
        const key = e.key.toUpperCase();
        if (["A", "B", "C", "D"].includes(key)) {
            selectOption(key);
        } else if (e.key === "Enter") {
            if (answered) nextQuestion();
            else if (selectedAnswer) submitAnswer();
        }
    });

    // ==================== 初始化 ====================

    updateStatsSummary();
})();
