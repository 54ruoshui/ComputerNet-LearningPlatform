/**
 * 教师端班级学情前端逻辑（只读）
 * - 列表视图：班级汇总卡片 + 学生列表（fetch /api/teacher/students）
 * - 详情视图：单学生概览（统计卡/章节热力图/各层掌握率/薄弱点/活动）+ LLM 学习报告
 */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    let currentStudent = null; // {session_id, nickname}
    let currentReport = "";
    let currentStudents = []; // 列表数据，供行点击委托取回（昵称等不再进内联 onclick）

    async function apiGet(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }
    async function apiPost(url) {
        const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }

    function esc(s) {
        const div = document.createElement("div");
        div.textContent = s == null ? "" : String(s);
        return div.innerHTML;
    }

    function showToast(msg, type) {
        const toast = $("toast");
        toast.textContent = msg;
        toast.className = "quiz-toast " + type + " show";
        setTimeout(() => { toast.className = "quiz-toast"; }, 3000);
    }

    function fmtTime(iso) {
        if (!iso) return "-";
        const d = new Date(iso);
        if (isNaN(d)) return String(iso).slice(0, 16).replace("T", " ");
        const pad = (n) => String(n).padStart(2, "0");
        return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    const ROLE_NAMES = { teacher: "教师", admin: "管理员" };

    // ==================== 列表视图 ====================

    async function loadStudents() {
        const tbody = $("studentRows");
        tbody.innerHTML = '<tr><td colspan="8"><div class="quiz-empty"><p>加载中...</p></div></td></tr>';
        let data;
        try {
            data = await apiGet("/api/teacher/students");
        } catch (e) {
            tbody.innerHTML = '<tr><td colspan="8"><div class="quiz-empty"><p>加载失败：' + esc(e.message) + "（需教师或管理员登录）</p></div></td></tr>";
            return;
        }
        const students = data.students || [];
        currentStudents = students;
        renderSummary(students);
        if (!students.length) {
            tbody.innerHTML = '<tr><td colspan="8"><div class="quiz-empty"><p>暂无学生学习记录</p></div></td></tr>';
            return;
        }
        tbody.innerHTML = students.map((s) => {
            const isGuest = s.user_role == null && s.nickname === "游客";
            const avatarCls = "teacher-avatar" + (isGuest ? " guest" : "");
            const initial = isGuest ? '<i class="fas fa-user" style="font-size:11px;"></i>' : esc(String(s.nickname).charAt(0).toUpperCase());
            const roleBadge = ROLE_NAMES[s.user_role]
                ? '<span class="teacher-role-badge">' + ROLE_NAMES[s.user_role] + "</span>"
                : "";
            const mastery = s.mastered_total ? s.mastered_count + "/" + s.mastered_total : "-";
            return (
                "<tr class=\"teacher-row\" data-sid=\"" + esc(s.session_id) + "\">" +
                '<td><div class="teacher-student-cell">' +
                '<span class="' + avatarCls + '">' + initial + "</span>" +
                '<span class="teacher-nickname">' + esc(s.nickname) + "</span>" + roleBadge +
                "</div></td>" +
                '<td class="num">' + s.done_count + "</td>" +
                '<td class="num">' + (s.wrong_count || 0) + "</td>" +
                '<td class="num">' + mastery + "</td>" +
                '<td class="num">' + s.due_count + "</td>" +
                '<td class="num">' + (s.exam_count || 0) + "</td>" +
                '<td class="teacher-time">' + esc(fmtTime(s.last_active)) + "</td>" +
                '<td><button class="teacher-view-btn"><i class="fas fa-chevron-right"></i> 查看</button></td>' +
                "</tr>"
            );
        }).join("");
    }

    function renderSummary(students) {
        $("statStudents").textContent = students.length;
        const sum = (fn) => students.reduce((acc, s) => acc + (fn(s) || 0), 0);
        $("statDone").textContent = sum((s) => s.done_count);
        $("statWrong").textContent = sum((s) => s.wrong_count);
        $("statDue").textContent = sum((s) => s.due_count);
        $("statMastered").textContent = sum((s) => s.mastered_count);
    }

    // ==================== 详情视图 ====================

    function openDetail(sessionId, nickname) {
        currentStudent = { session_id: sessionId, nickname };
        $("listView").style.display = "none";
        $("detailView").style.display = "block";
        $("detailNickname").textContent = nickname;
        $("detailSession").textContent = sessionId;
        window.scrollTo(0, 0);
        loadOverview(sessionId);
    }

    function backToList() {
        currentStudent = null;
        $("detailView").style.display = "none";
        $("listView").style.display = "block";
        loadStudents();
    }

    async function loadOverview(sessionId) {
        try {
            const data = await apiGet("/api/teacher/students/" + encodeURIComponent(sessionId) + "/overview");
            renderDetailStats(data);
            renderHeatmap(data.sections || []);
            renderLayers((data.mastery || {}).layers || []);
            renderWeak(data.weak_points || []);
            renderActivity(data.activity || []);
        } catch (e) {
            showToast("学情加载失败：" + e.message, "error");
        }
    }

    function renderDetailStats(data) {
        const mastery = data.mastery || {};
        const review = data.review || {};
        $("dTotal").textContent = mastery.total != null ? mastery.total : "-";
        $("dMastered").textContent = mastery.mastered != null ? mastery.mastered : "-";
        $("dLearning").textContent = mastery.learning != null ? mastery.learning : "-";
        $("dDue").textContent = review.due_now != null ? review.due_now : "-";
        const trend = data.exam_trend || [];
        $("dAccuracy").textContent = trend.length
            ? (trend.reduce((s, t) => s + (t.accuracy || 0), 0) / trend.length).toFixed(1) + "%"
            : "-";
    }

    function heatColor(rate) {
        if (rate == null) return "var(--bg-3)";
        if (rate >= 80) return "rgba(52,199,123,1)";
        if (rate >= 60) return "rgba(52,199,123,0.8)";
        if (rate >= 40) return "rgba(52,199,123,0.55)";
        if (rate >= 20) return "rgba(52,199,123,0.35)";
        return "rgba(52,199,123,0.15)";
    }

    function renderHeatmap(sections) {
        const container = $("dHeatmap");
        if (!sections.length) {
            container.innerHTML = '<div class="quiz-empty"><p>暂无章节数据</p></div>';
            return;
        }
        container.innerHTML = sections.map((s) => {
            const title = s.rate != null
                ? `${s.chapter} ${s.section} — 答题正确率 ${s.rate}%（${s.correct}/${s.total}）`
                : `${s.chapter} ${s.section} — 暂无答题数据`;
            return (
                '<div class="heatmap-cell' + (s.rate == null ? " no-data" : "") + '"' +
                ' title="' + esc(title) + '" style="background:' + heatColor(s.rate) + ';">' +
                '<span class="cell-chapter">' + esc(s.chapter) + "</span>" +
                '<span class="cell-rate">' + (s.rate != null ? s.rate + "%" : "—") + "</span>" +
                "</div>"
            );
        }).join("");
    }

    function renderLayers(layers) {
        const container = $("dLayers");
        if (!layers.length) {
            container.innerHTML = '<div class="quiz-empty"><p>暂无层级数据</p></div>';
            return;
        }
        container.innerHTML = layers.map((l) => {
            const pct = Math.round(l.rate * 100);
            return (
                '<div class="layer-bar-row">' +
                '<span class="layer-bar-name" title="' + esc(l.layer) + '">' + esc(l.layer) + "</span>" +
                '<div class="layer-bar-track"><div class="layer-bar-fill" style="width:' + pct + '%;"></div></div>' +
                '<span class="layer-bar-meta">' + pct + "% · " + l.mastered + "/" + l.total + " 掌握</span>" +
                "</div>"
            );
        }).join("");
    }

    function renderWeak(points) {
        const container = $("dWeak");
        if (!points.length) {
            container.innerHTML = '<div class="quiz-empty"><p>暂无薄弱点数据，完成考试后自动分析</p></div>';
            return;
        }
        const catNames = { layer: "层次", difficulty: "难度", chapter: "章节" };
        let html = '<table class="weak-table"><thead><tr><th>类型</th><th>薄弱点</th><th>错误次数</th><th>占比</th></tr></thead><tbody>';
        points.forEach((p) => {
            html +=
                "<tr>" +
                '<td><span class="weak-cat-badge ' + esc(p.category) + '">' + (catNames[p.category] || p.category) + "</span></td>" +
                "<td>" + esc(p.value) + "</td>" +
                "<td>" + p.error_count + "</td>" +
                '<td><div class="weak-pct-track"><div class="weak-pct-fill" style="width:' + p.percentage + '%;"></div></div>' +
                p.percentage + "%</td>" +
                "</tr>";
        });
        html += "</tbody></table>";
        container.innerHTML = html;
    }

    function renderActivity(list) {
        const container = $("dActivity");
        if (!list.length) {
            container.innerHTML = '<div class="quiz-empty"><p>暂无活动记录</p></div>';
            return;
        }
        container.innerHTML = list.map((a) => {
            const isReview = a.type === "review";
            return (
                '<div class="timeline-item">' +
                '<div class="timeline-icon ' + esc(a.type) + '">' +
                '<i class="fas ' + (isReview ? "fa-rotate" : "fa-file-pen") + '"></i></div>' +
                '<div class="timeline-body">' +
                '<div class="timeline-detail">' + esc(a.detail) + "</div>" +
                '<div class="timeline-time">' + esc(a.time) + "</div>" +
                "</div></div>"
            );
        }).join("");
    }

    // ==================== 学习报告 ====================

    async function exportDetailReport() {
        if (!currentStudent) return;
        const btn = $("btnDetailReport");
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 生成中...';
        try {
            const data = await apiPost("/api/teacher/students/" + encodeURIComponent(currentStudent.session_id) + "/report");
            if (!data.report) {
                showToast("报告生成失败，请稍后重试", "error");
                return;
            }
            currentReport = data.report;
            $("reportContent").textContent = currentReport;
            $("reportModal").style.display = "flex";
        } catch (e) {
            showToast("报告生成失败：" + e.message, "error");
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> 生成学习报告（LLM）';
        }
    }

    function closeReportModal() { $("reportModal").style.display = "none"; }

    async function copyReport() {
        try {
            await navigator.clipboard.writeText(currentReport);
            showToast("已复制到剪贴板", "success");
        } catch (e) {
            showToast("复制失败，请手动选择文本", "error");
        }
    }

    function downloadReport() {
        const blob = new Blob([currentReport], { type: "text/markdown;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "学习报告_" + (currentStudent ? currentStudent.nickname : "") + ".md";
        a.click();
        URL.revokeObjectURL(a.href);
    }

    $("reportModal").addEventListener("click", (e) => {
        if (e.target === $("reportModal")) closeReportModal();
    });

    // 暴露给内联 onclick
    window.loadStudents = loadStudents;
    window.backToList = backToList;
    window.exportDetailReport = exportDetailReport;
    window.closeReportModal = closeReportModal;
    window.copyReport = copyReport;
    window.downloadReport = downloadReport;

    // ==================== 初始化 ====================

    $("studentRows").addEventListener("click", (e) => {
        const tr = e.target.closest("tr[data-sid]");
        if (!tr) return;
        const s = currentStudents.find((x) => x.session_id === tr.dataset.sid);
        if (s) openDetail(s.session_id, s.nickname);
    });

    loadStudents();
})();
