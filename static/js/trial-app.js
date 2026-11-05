/**
 * 前测-后测试用评估前端
 * - 研究管理：创建批次（平行卷）、登记被试、效果分析、LLM 评估报告
 * - 被试入口：凭码加入 → 前测 → 系统学习期 → 后测 → 查看成绩
 */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    // ==================== 状态 ====================

    let currentTab = "admin";
    let selectedTrialId = null;
    let selectedTrialName = "";
    let lastReport = "";

    const partState = {
        code: null,
        preDone: false,
        postDone: false,
    };

    const testState = {
        testId: null,
        phase: "pre",
        questions: [],
        currentIdx: 0,
        answers: {},
    };

    // 作答进度本地暂存：刷新/误触返回后可恢复（交卷成功即清除；按 被试码+阶段+testId 匹配）
    const TRIAL_PROGRESS_KEY = "trial_test_progress";

    function saveTrialProgress() {
        if (!testState.testId || !testState.questions.length || !partState.code) return;
        try {
            localStorage.setItem(TRIAL_PROGRESS_KEY, JSON.stringify({
                key: partState.code + ":" + testState.phase,
                testId: testState.testId,
                answers: testState.answers,
                currentIdx: testState.currentIdx,
                savedAt: Date.now(),
            }));
        } catch (e) { /* 存储失败不阻断作答 */ }
    }

    function clearTrialProgress() {
        try { localStorage.removeItem(TRIAL_PROGRESS_KEY); } catch (e) {}
    }

    function restoreTrialProgress() {
        try {
            const raw = localStorage.getItem(TRIAL_PROGRESS_KEY);
            if (!raw) return false;
            const p = JSON.parse(raw);
            if (!p || p.key !== partState.code + ":" + testState.phase || p.testId !== testState.testId) return false;
            if (!p.answers || !Object.keys(p.answers).length) return false;
            testState.answers = p.answers;
            testState.currentIdx = Math.min(p.currentIdx || 0, testState.questions.length - 1);
            return true;
        } catch (e) {
            return false;
        }
    }

    // ==================== API ====================

    async function apiGet(url, headers) {
        const res = await fetch(url, { headers: headers || {} });
        if (!res.ok) throw new Error(await errDetail(res));
        return res.json();
    }

    async function apiPost(url, body, headers) {
        const opts = { method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, headers || {}) };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const res = await fetch(url, opts);
        if (!res.ok) throw new Error(await errDetail(res));
        return res.json();
    }

    function adminHeaders() {
        const t = localStorage.getItem("aigc_admin_token") || "";
        return t ? { "X-Admin-Token": t } : {};
    }

    async function errDetail(res) {
        try {
            const data = await res.json();
            if (data && data.detail) return String(data.detail);
        } catch (e) { /* ignore */ }
        return `HTTP ${res.status}`;
    }

    function showToast(msg, type) {
        const wrap = $("toastWrap");
        if (!wrap) return;
        while (wrap.children.length >= 5) wrap.firstChild.remove();  // 防刷屏
        const toast = document.createElement("div");
        toast.className = "quiz-toast " + (type || "success");
        toast.textContent = msg;
        wrap.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add("show"));
        setTimeout(() => {
            toast.classList.remove("show");
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    function escapeHtml(str) {
        if (!str) return "";
        return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function pct(v) {
        return (v === null || v === undefined) ? "—" : Math.round(v * 100) + "%";
    }

    function sessionId() {
        return localStorage.getItem("graphrag_session_id") || "default";
    }

    // ==================== Tab 切换 ====================

    window.switchTrialTab = function (tab) {
        currentTab = tab;
        $("tabAdmin").classList.toggle("active", tab === "admin");
        $("tabParticipant").classList.toggle("active", tab === "participant");
        $("adminPanel").style.display = tab === "admin" ? "block" : "none";
        $("participantPanel").style.display = tab === "participant" ? "block" : "none";
        if (tab === "admin") loadTrials();
        if (tab === "participant") restoreParticipant();
    };

    // ==================== 研究管理：批次列表 ====================

    window.saveAdminToken = function () {
        const t = $("adminTokenInput").value.trim();
        if (!t) { showToast("请输入管理口令", "error"); return; }
        localStorage.setItem("aigc_admin_token", t);
        showToast("口令已保存", "success");
        loadTrials();
    };

    async function loadTrials() {
        const input = $("adminTokenInput");
        if (input) input.value = localStorage.getItem("aigc_admin_token") || "";
        try {
            const data = await apiGet("/api/trial/list", adminHeaders());
            renderTrials(data.trials || []);
        } catch (e) {
            $("trialList").innerHTML = '<p class="trial-hint">请先在上方输入管理口令，才能查看和管理试用批次。</p>';
        }
    }

    function renderTrials(trials) {
        if (!trials.length) {
            $("trialList").innerHTML = '<p class="trial-hint">还没有试用批次，先创建一个。</p>';
            return;
        }
        let html = "";
        for (const t of trials) {
            html += '<div class="trial-card" onclick="openTrialDetail(\'' + t.id + '\')">' +
                '<div class="trial-card-icon"><i class="fas fa-clipboard-list"></i></div>' +
                '<div class="trial-card-body">' +
                    '<div class="trial-card-title">' + escapeHtml(t.name) + "</div>" +
                    '<div class="trial-card-meta">' +
                        '<span><i class="fas fa-users"></i> 被试 ' + t.participant_count + "</span>" +
                        '<span><i class="fas fa-file-circle-check"></i> 已交卷 ' + t.submitted_test_count + "</span>" +
                        "<span>" + escapeHtml(t.created_at || "") + "</span>" +
                        (t.status !== "active" ? '<span class="trial-tag-closed">已结束</span>' : "") +
                    "</div>" +
                "</div>" +
                '<div class="paper-arrow"><i class="fas fa-chevron-right"></i></div>' +
            "</div>";
        }
        $("trialList").innerHTML = html;
    }

    window.createTrial = async function () {
        const name = $("trialName").value.trim();
        if (!name) { showToast("请填写批次名称", "error"); return; }
        const perLayer = parseInt($("trialPerLayer").value, 10) || 4;
        const seedRaw = $("trialSeed").value.trim();
        const body = { name: name, per_layer: perLayer };
        if (seedRaw !== "") body.seed = parseInt(seedRaw, 10);
        $("createTrialBtn").disabled = true;
        try {
            const created = await apiPost("/api/trial/create", body, adminHeaders());
            showToast("批次已创建：前测/后测各 " + created.question_count + " 题（seed=" + created.seed + "）", "success");
            $("trialName").value = "";
            loadTrials();
        } catch (e) {
            showToast("创建失败：" + e.message, "error");
        } finally {
            $("createTrialBtn").disabled = false;
        }
    };

    // ==================== 研究管理：批次详情 ====================

    window.openTrialDetail = async function (trialId) {
        selectedTrialId = trialId;
        try {
            const data = await apiGet("/api/trial/" + encodeURIComponent(trialId) + "/participants", adminHeaders());
            $("adminListView").style.display = "none";
            $("adminAnalysisView").style.display = "none";
            $("adminReportView").style.display = "none";
            $("adminDetailView").style.display = "block";
            selectedTrialName = "";
            renderParticipants(data.participants || [], trialId);
        } catch (e) {
            showToast("加载批次失败：" + e.message, "error");
        }
    };

    function renderParticipants(participants, trialId) {
        $("detailTrialName").textContent = "批次 " + trialId;
        $("detailTrialMeta").innerHTML =
            "<span><i class=\"fas fa-users\"></i> 被试 " + participants.length + "</span>" +
            "<span><i class=\"fas fa-flag-checkered\"></i> 完成双测 " +
            participants.filter((p) => p.pre_done && p.post_done).length + "</span>";

        if (!participants.length) {
            $("participantTable").innerHTML = '<p class="trial-hint">暂无被试，点击上方按钮添加。</p>';
            return;
        }
        let html = '<table class="trial-table"><thead><tr>' +
            "<th>参试码</th><th>会话</th><th>前测</th><th>后测</th><th>登记时间</th></tr></thead><tbody>";
        for (const p of participants) {
            html += "<tr>" +
                "<td><code>" + escapeHtml(p.code) + "</code></td>" +
                "<td>" + (p.session_id ? escapeHtml(p.session_id) : "<span class='trial-muted'>未绑定</span>") + "</td>" +
                "<td>" + mark(p.pre_done) + "</td>" +
                "<td>" + mark(p.post_done) + "</td>" +
                "<td class='trial-muted'>" + escapeHtml(p.created_at || "") + "</td>" +
            "</tr>";
        }
        html += "</tbody></table>";
        $("participantTable").innerHTML = html;
    }

    function mark(done) {
        return done ? '<span class="trial-mark ok"><i class="fas fa-check"></i></span>'
                    : '<span class="trial-mark no"><i class="fas fa-minus"></i></span>';
    }

    window.backToTrialList = function () {
        $("adminDetailView").style.display = "none";
        $("adminAnalysisView").style.display = "none";
        $("adminReportView").style.display = "none";
        $("adminListView").style.display = "block";
        loadTrials();
    };

    window.addParticipant = async function () {
        try {
            const p = await apiPost("/api/trial/" + encodeURIComponent(selectedTrialId) + "/participants", {}, adminHeaders());
            showToast("已添加被试，参试码：" + p.code, "success");
            openTrialDetail(selectedTrialId);
        } catch (e) {
            showToast("添加失败：" + e.message, "error");
        }
    };

    // ==================== 研究管理：效果分析 ====================

    window.showAnalysis = async function () {
        $("adminDetailView").style.display = "none";
        $("adminReportView").style.display = "none";
        $("adminAnalysisView").style.display = "block";
        $("analysisBody").innerHTML = '<p class="trial-hint">加载中...</p>';
        try {
            const a = await apiGet("/api/trial/" + encodeURIComponent(selectedTrialId) + "/analysis", adminHeaders());
            renderAnalysis(a);
        } catch (e) {
            $("analysisBody").innerHTML = '<p class="trial-hint">加载失败：' + escapeHtml(e.message) + "</p>";
        }
    };

    function renderAnalysis(a) {
        const agg = a.aggregate || {};
        const completed = (a.participants || []).filter((p) => p.completed);
        const pending = (a.participants || []).filter((p) => !p.completed);

        let html = '<div class="trial-stats-grid">';
        html += statCard("fas fa-users", "有效被试", agg.n === 0 ? "0" : String(agg.n),
            pending.length ? pending.length + " 人未完成双测" : "全部完成");
        html += statCard("fas fa-arrow-right-arrow-left", "前测 → 后测",
            agg.mean_pre === null ? "—" : pct(agg.mean_pre) + " → " + pct(agg.mean_post), "平均正确率");
        html += statCard("fas fa-arrow-trend-up", "平均提升",
            agg.mean_gain === null ? "—" : (agg.mean_gain >= 0 ? "+" : "") + Math.round(agg.mean_gain * 100) + " pp",
            agg.std_gain !== null && agg.std_gain !== undefined ? "标准差 " + Math.round(agg.std_gain * 100) + " pp" : "");
        html += statCard("fas fa-percent", "提升率",
            agg.improvement_rate === null ? "—" : pct(agg.improvement_rate), "后测提高的被试占比");
        html += "</div>";

        if (agg.small_sample_caveat) {
            html += '<div class="trial-caveat"><i class="fas fa-triangle-exclamation"></i> 有效样本 n=' + agg.n +
                " &lt; 5，结果仅为初步观察，不具统计显著性。</div>";
        }

        const layers = agg.layer_gain || {};
        const layerKeys = Object.keys(layers);
        if (layerKeys.length) {
            html += '<div class="section-title"><i class="fas fa-layer-group"></i> 分层提升</div><table class="trial-table">' +
                "<thead><tr><th>层次</th><th>前测均值</th><th>后测均值</th><th>提升</th></tr></thead><tbody>";
            for (const k of layerKeys) {
                const g = layers[k];
                const gain = Math.round(g.mean_gain * 100);
                html += "<tr><td>" + escapeHtml(k) + "</td><td>" + pct(g.mean_pre) + "</td><td>" + pct(g.mean_post) +
                    '</td><td><span class="' + (gain > 0 ? "trial-gain-up" : (gain < 0 ? "trial-gain-down" : "")) + '">' +
                    (gain > 0 ? "+" : "") + gain + " pp</span></td></tr>";
            }
            html += "</tbody></table>";
        }

        if (completed.length) {
            html += '<div class="section-title"><i class="fas fa-user-pen"></i> 被试明细（完成双测）</div>';
            html += renderParticipantRows(completed, true);
        }
        if (pending.length) {
            html += '<div class="section-title"><i class="fas fa-hourglass-half"></i> 未完成双测</div>';
            html += renderParticipantRows(pending, false);
        }
        $("analysisBody").innerHTML = html;
    }

    function renderParticipantRows(list, detailed) {
        let html = '<table class="trial-table"><thead><tr>';
        if (detailed) {
            html += "<th>参试码</th><th>前测</th><th>后测</th><th>提升</th><th>答题</th><th>复习</th><th>覆盖知识点</th><th>满意度</th>";
        } else {
            html += "<th>参试码</th><th>前测</th><th>后测</th><th>会话</th>";
        }
        html += "</tr></thead><tbody>";
        for (const p of list) {
            html += "<tr><td><code>" + escapeHtml(p.code) + "</code></td>";
            if (detailed) {
                const gain = p.gain === null ? null : Math.round(p.gain * 100);
                const quizN = p.behavior ? ((p.behavior.by_type.quiz_correct || {}).count || 0) +
                    ((p.behavior.by_type.quiz_wrong || {}).count || 0) : 0;
                const manualN = p.behavior ? ((p.behavior.by_type.manual || {}).count || 0) : 0;
                html += "<td>" + (p.pre && p.pre.accuracy !== undefined ? pct(p.pre.accuracy) : "—") + "</td>" +
                    "<td>" + (p.post && p.post.accuracy !== undefined ? pct(p.post.accuracy) : "—") + "</td>" +
                    '<td><span class="' + (gain > 0 ? "trial-gain-up" : (gain < 0 ? "trial-gain-down" : "")) + '">' +
                    (gain === null ? "—" : (gain > 0 ? "+" : "") + gain + " pp") + "</span></td>" +
                    "<td>" + quizN + "</td><td>" + manualN + "</td>" +
                    "<td>" + (p.behavior ? p.behavior.total_entities : 0) + "</td>" +
                    "<td>" + (p.satisfaction && p.satisfaction.total ?
                        p.satisfaction.positive + "/" + p.satisfaction.total : "—") + "</td>";
            } else {
                html += "<td>" + (p.pre && p.pre.status === "submitted" ? "已交" : (p.pre ? "进行中" : "未开始")) + "</td>" +
                    "<td>" + (p.post && p.post.status === "submitted" ? "已交" : (p.post ? "进行中" : "未开始")) + "</td>" +
                    "<td>" + (p.session_id ? escapeHtml(p.session_id) : "<span class='trial-muted'>未绑定</span>") + "</td>";
            }
            html += "</tr>";
        }
        return html + "</tbody></table>";
    }

    function statCard(icon, label, value, sub) {
        return '<div class="trial-stat-card"><div class="stat-icon"><i class="' + icon + '"></i></div>' +
            '<div class="stat-body"><div class="stat-value">' + value + "</div>" +
            '<div class="stat-label">' + escapeHtml(label) + (sub ? " · " + escapeHtml(sub) : "") + "</div></div></div>";
    }

    // ==================== 研究管理：评估报告 ====================

    window.generateReport = async function () {
        $("adminDetailView").style.display = "none";
        $("adminAnalysisView").style.display = "none";
        $("adminReportView").style.display = "block";
        $("reportBody").innerHTML = '<p class="trial-hint"><i class="fas fa-spinner fa-spin"></i> LLM 生成中，请稍候...</p>';
        try {
            const data = await apiPost("/api/trial/" + encodeURIComponent(selectedTrialId) + "/report", undefined, adminHeaders());
            lastReport = data.report || "";
            $("reportBody").innerHTML = '<pre class="trial-report">' + escapeHtml(lastReport) + "</pre>";
        } catch (e) {
            $("reportBody").innerHTML = '<p class="trial-hint">生成失败：' + escapeHtml(e.message) + "</p>";
        }
    };

    window.copyReport = function () {
        if (!lastReport) { showToast("暂无报告内容", "error"); return; }
        navigator.clipboard.writeText(lastReport).then(
            () => showToast("已复制到剪贴板", "success"),
            () => showToast("复制失败，请手动选择文本", "error")
        );
    };

    // ==================== 被试：加入与状态 ====================

    function restoreParticipant() {
        const saved = localStorage.getItem("trial_participant_code");
        if (saved && !partState.code) {
            $("joinCode").value = saved;
            joinTrial(true);
        }
    }

    window.joinTrial = async function (silent) {
        const code = $("joinCode").value.trim();
        if (!code) { showToast("请输入参试码", "error"); return; }
        try {
            const data = await apiPost("/api/trial/join", { code: code, session_id: sessionId() });
            // 服务端协商出的会话可能不同于本地（共用电脑换码/换设备找回），以服务端为准
            if (data.session_id) localStorage.setItem("graphrag_session_id", data.session_id);
            partState.code = code;
            partState.preDone = data.pre_done;
            partState.postDone = data.post_done;
            localStorage.setItem("trial_participant_code", code);
            renderPartStatus();
            if (!silent) showToast("已加入批次：" + (data.participant.trial_name || ""), "success");
        } catch (e) {
            if (!silent) showToast("加入失败：" + e.message, "error");
        }
    };

    function renderPartStatus() {
        $("partJoinView").style.display = "none";
        $("partStatusView").style.display = "block";
        $("partTakeView").style.display = "none";
        $("partResultView").style.display = "none";

        $("partCodeLabel").textContent = partState.code;
        $("btnPre").disabled = partState.preDone;
        $("btnPre").innerHTML = partState.preDone
            ? '<i class="fas fa-check"></i> 已完成'
            : '<i class="fas fa-play"></i> 开始前测';
        $("btnPost").disabled = !partState.preDone || partState.postDone;
        $("btnPost").innerHTML = partState.postDone
            ? '<i class="fas fa-check"></i> 已完成'
            : '<i class="fas fa-flag-checkered"></i> 开始后测';
        $("preStepDesc").textContent = partState.preDone ? "已完成" : "未开始";
        $("postStepDesc").textContent = partState.postDone ? "已完成" :
            (partState.preDone ? "已解锁，可开始" : "完成前测后解锁");
        $("stepPre").classList.toggle("done", partState.preDone);
        $("stepPost").classList.toggle("done", partState.postDone);
    }

    window.backToPartStatus = function () {
        // 交卷后刷新状态（join 幂等，可重入拿最新状态）
        if (partState.code) {
            $("joinCode").value = partState.code;
            joinTrial(true);
        }
    };

    // ==================== 被试：作答（仿考试模式） ====================

    window.startTrialTest = async function (phase) {
        if (!partState.code) return;
        try {
            const data = await apiPost("/api/trial/test/start", { code: partState.code, phase: phase });
            testState.testId = data.test_id;
            testState.phase = phase;
            testState.questions = data.questions || [];
            testState.currentIdx = 0;
            testState.answers = {};

            $("partStatusView").style.display = "none";
            $("partResultView").style.display = "none";
            $("partTakeView").style.display = "flex";
            $("trialNav").style.display = "none";
            $("takeTitle").textContent = phase === "pre" ? "前测" : "后测";
            // 先恢复暂存进度再渲染，否则首次渲染会把暂存覆盖成空
            const restored = restoreTrialProgress();
            renderTakeQuestion();
            renderTrialNav();
            if (restored) showToast("已恢复上次作答进度", "success");
        } catch (e) {
            showToast(e.message, "error");
        }
    };

    function renderTakeQuestion() {
        const q = testState.questions[testState.currentIdx];
        const total = testState.questions.length;
        $("takeProgress").textContent = "第 " + (testState.currentIdx + 1) + " 题 / 共 " + total + " 题";
        const diffLabel = { basic: "基础", medium: "中等", hard: "困难" };
        const meta = [];
        if (q.layer) meta.push(q.layer);
        if (q.difficulty) meta.push(diffLabel[q.difficulty] || q.difficulty);
        $("takeMeta").textContent = meta.join(" · ");

        $("takeQuestionText").textContent = q.text;

        const options = ["A", "B", "C", "D"];
        const optionTexts = { A: q.option_a, B: q.option_b, C: q.option_c, D: q.option_d };
        const existing = testState.answers[q.id];
        let html = "";
        for (let i = 0; i < options.length; i++) {
            const letter = options[i];
            const selected = existing === letter ? " selected" : "";
            html += '<button class="option-btn' + selected + '" data-letter="' + letter +
                '" onclick="selectTrialOption(\'' + letter + '\')">' +
                '<span class="option-letter">' + letter + "</span>" +
                "<span>" + escapeHtml(optionTexts[letter] || "") + "</span></button>";
        }
        $("takeOptionsList").innerHTML = html;

        $("takePrevBtn").style.visibility = testState.currentIdx > 0 ? "visible" : "hidden";
        $("takeNextBtn").innerHTML = testState.currentIdx < total - 1
            ? '下一题 <i class="fas fa-chevron-right"></i>'
            : '交卷 <i class="fas fa-check"></i>';

        const answered = Object.keys(testState.answers).length;
        $("takeAnsweredBadge").textContent = "已答 " + answered + "/" + total;
        saveTrialProgress();
    }

    window.selectTrialOption = function (letter) {
        const q = testState.questions[testState.currentIdx];
        testState.answers[q.id] = letter;
        document.querySelectorAll("#takeOptionsList .option-btn").forEach(function (btn) {
            btn.classList.toggle("selected", btn.dataset.letter === letter);
        });
        const answered = Object.keys(testState.answers).length;
        $("takeAnsweredBadge").textContent = "已答 " + answered + "/" + testState.questions.length;
        saveTrialProgress();
    };

    function renderTrialNav() {
        let html = "";
        for (let i = 0; i < testState.questions.length; i++) {
            const qid = testState.questions[i].id;
            let cls = "exam-nav-btn";
            if (i === testState.currentIdx) cls += " current";
            else if (testState.answers[qid]) cls += " answered";
            html += '<button class="' + cls + '" onclick="jumpTrialQuestion(' + i + ')">' + (i + 1) + "</button>";
        }
        $("trialNavGrid").innerHTML = html;
    }

    window.toggleTrialNav = function () {
        const nav = $("trialNav");
        nav.style.display = nav.style.display === "none" ? "block" : "none";
        if (nav.style.display !== "none") renderTrialNav();
    };

    window.jumpTrialQuestion = function (idx) {
        testState.currentIdx = idx;
        renderTakeQuestion();
        renderTrialNav();
        $("trialNav").style.display = "none";
    };

    window.trialNextQuestion = function () {
        const total = testState.questions.length;
        if (testState.currentIdx >= total - 1) {
            let unanswered = 0;
            for (let i = 0; i < total; i++) {
                if (!testState.answers[testState.questions[i].id]) unanswered++;
            }
            let msg = "确定要交卷吗？";
            if (unanswered > 0) msg += "\n还有 " + unanswered + " 道题未作答。";
            if (!confirm(msg)) return;
            submitTrialTest();
            return;
        }
        testState.currentIdx++;
        renderTakeQuestion();
        renderTrialNav();
    };

    window.trialPrevQuestion = function () {
        if (testState.currentIdx > 0) {
            testState.currentIdx--;
            renderTakeQuestion();
            renderTrialNav();
        }
    };

    let trialSubmitting = false;

    window.submitTrialTest = async function () {
        if (trialSubmitting) return;  // 双击/最后一题两个入口防重
        trialSubmitting = true;
        try {
            $("takeSubmitBtn").disabled = true;
            $("takeSubmitBtn").textContent = "提交中...";
            $("takeNextBtn").disabled = true;
            const result = await apiPost("/api/trial/test/submit", {
                test_id: testState.testId,
                code: partState.code,
                answers: testState.answers,
            });
            clearTrialProgress();
            testState.questions = [];
            testState.answers = {};
            $("partTakeView").style.display = "none";
            $("partResultView").style.display = "block";
            renderPartResult(result);
        } catch (e) {
            showToast("提交失败：" + e.message, "error");
            trialSubmitting = false;
            $("takeSubmitBtn").disabled = false;
            $("takeSubmitBtn").innerHTML = '<i class="fas fa-check"></i> 交卷';
            $("takeNextBtn").disabled = false;
        }
    };

    // 作答进行中离开页面前确认（配合 localStorage 暂存双保险）
    window.addEventListener("beforeunload", function (e) {
        if (testState.questions.length && Object.keys(testState.answers).length) {
            e.preventDefault();
            e.returnValue = "";
        }
    });

    function renderPartResult(result) {
        const accuracy = Math.round((result.accuracy || 0) * 100);
        const accClass = accuracy >= 80 ? "high" : (accuracy >= 60 ? "medium" : "low");
        const phaseLabel = result.phase === "pre" ? "前测" : "后测";

        const circumference = 2 * Math.PI * 52;
        const dashOffset = circumference * (1 - accuracy / 100);
        $("partResultScore").innerHTML =
            '<div class="exam-score-ring-wrap"><div class="exam-score-ring">' +
            '<svg width="140" height="140" viewBox="0 0 140 140">' +
                '<circle class="ring-bg" cx="70" cy="70" r="52"/>' +
                '<circle class="ring-fill ' + accClass + '" cx="70" cy="70" r="52" ' +
                    'stroke-dasharray="' + circumference + '" stroke-dashoffset="' + circumference + '" id="trialRingFill"/>' +
            "</svg>" +
            '<div class="ring-center"><div class="ring-score">' + result.correct_count + '</div>' +
            '<div class="ring-total">/ ' + result.total_count + "</div>" +
            '<div class="ring-pct ' + accClass + '">' + accuracy + "%</div></div></div></div>" +
            '<div class="exam-result-meta">' +
                '<div class="meta-item">' + phaseLabel + "成绩</div>" +
                '<div class="meta-item"><span class="meta-value green">' + result.correct_count + "</span>正确</div>" +
                '<div class="meta-item"><span class="meta-value red">' +
                (result.total_count - result.correct_count) + "</span>错误</div>" +
            "</div>";
        setTimeout(function () {
            const ring = document.getElementById("trialRingFill");
            if (ring) ring.style.strokeDashoffset = dashOffset;
        }, 100);

        const details = result.details || [];
        let html = '<div class="stats-title" style="margin-top:16px;"><i class="fas fa-list-check"></i> 逐题详情</div>';
        for (let i = 0; i < details.length; i++) {
            const d = details[i];
            const statusCls = d.correct ? "correct" : "wrong";
            const statusText = d.correct ? "✓ 正确" : "✗ 错误";
            html += '<div class="exam-detail-item ' + (d.correct ? "correct-detail" : "wrong-detail") + '">' +
                '<button class="exam-detail-toggle' + (d.correct ? "" : " open") + '" onclick="toggleTrialDetail(this, ' + i + ')" style="cursor:pointer;">' +
                    '<span class="detail-status ' + statusCls + '">' + statusText + "</span>" +
                    '<span class="detail-q-text">' + escapeHtml(d.text || "第 " + (i + 1) + " 题") + "</span>" +
                    '<i class="fas fa-chevron-down detail-arrow"></i></button>' +
                '<div class="exam-detail-body' + (d.correct ? "" : " open") + '" id="trialDetailBody' + i + '">' +
                    (d.correct ? "" : '<div class="detail-label">你的答案：<span style="color:var(--red);">' +
                        escapeHtml(d.selected_answer || "未作答") + "</span></div>") +
                    '<div class="detail-label">正确答案：<span style="color:var(--green);">' +
                        escapeHtml(d.correct_answer || "") + "</span></div>" +
                    (d.explanation ? '<div class="detail-label">解析</div><div>' + escapeHtml(d.explanation) + "</div>" : "") +
                "</div></div>";
        }
        $("partResultDetails").innerHTML = html;
    }

    window.toggleTrialDetail = function (btn, idx) {
        const body = document.getElementById("trialDetailBody" + idx);
        const isOpen = body.classList.contains("open");
        document.querySelectorAll(".exam-detail-body.open").forEach(function (el) { el.classList.remove("open"); });
        document.querySelectorAll(".exam-detail-toggle.open").forEach(function (el) { el.classList.remove("open"); });
        if (!isOpen) {
            body.classList.add("open");
            btn.classList.add("open");
        }
    };

    // ==================== 键盘快捷键 ====================

    document.addEventListener("keydown", function (e) {
        if (currentTab === "participant" && $("partTakeView").style.display !== "none") {
            const key = e.key.toUpperCase();
            if (["A", "B", "C", "D"].includes(key)) { selectTrialOption(key); return; }
            if (e.key === "ArrowRight") { trialNextQuestion(); return; }
            if (e.key === "ArrowLeft") { trialPrevQuestion(); return; }
        }
    });

    // ==================== 初始化 ====================

    // 研究管理标签仅对管理员开放；已存 aigc_admin_token 的游客研究者（兼容现状）也可见
    async function initTabs() {
        let isAdmin = false;
        if (localStorage.getItem("aigc_admin_token")) {
            isAdmin = true;
        } else {
            try {
                const resp = await fetch("/api/auth/me");
                const data = await resp.json();
                isAdmin = !!(data.user && data.user.role === "admin");
            } catch (e) { /* 游客 */ }
        }
        if (isAdmin) {
            $("tabAdmin").style.display = "";
            $("adminPanel").style.display = "block";
            loadTrials();
        } else {
            switchTrialTab("participant");
        }
    }

    initTabs();
})();
