/**
 * 学习仪表盘前端逻辑
 * - 统计卡片 + 章节热力图 + 各层掌握率
 * - 考试正确率趋势 SVG + 复习日历
 * - 薄弱点 Top10 + 最近活动时间线
 * - LLM 学习报告（弹窗/复制/下载/打印）
 */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);

    // --- API helpers ---
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

    function getSessionId() {
        return localStorage.getItem("graphrag_session_id") || "default";
    }

    // --- Toast ---
    function showToast(msg, type) {
        const toast = $("toast");
        toast.textContent = msg;
        toast.className = "quiz-toast " + type + " show";
        setTimeout(() => { toast.className = "quiz-toast"; }, 3000);
    }

    function esc(s) {
        const div = document.createElement("div");
        div.textContent = s == null ? "" : String(s);
        return div.innerHTML;
    }

    // ==================== 数据加载 ====================

    async function loadOverview() {
        const sid = getSessionId();
        try {
            const data = await apiGet("/api/dashboard/overview/" + encodeURIComponent(sid));
            renderStats(data);
            renderHeatmap(data.sections || []);
            renderLayerMastery((data.mastery || {}).layers || []);
            renderLineChart(data.exam_trend || []);
            renderCalendar((data.review || {}).daily_trend || []);
            renderWeakPoints(data.weak_points || []);
            renderActivity(data.activity || []);
        } catch (e) {
            console.error("加载仪表盘数据失败", e);
            showToast("仪表盘数据加载失败：" + e.message, "error");
            const empty = (id, msg) => { $(id).innerHTML = '<div class="quiz-empty"><p>' + msg + "</p></div>"; };
            empty("heatmap", "加载失败");
            empty("layerMastery", "加载失败");
            empty("trendChart", "加载失败");
            empty("calendar", "加载失败");
            empty("weakPoints", "加载失败");
            empty("activity", "加载失败");
        }
    }

    // ==================== 统计卡片 ====================

    function renderStats(data) {
        const mastery = data.mastery || {};
        const review = data.review || {};
        $("statTotal").textContent = mastery.total != null ? mastery.total : "-";
        $("statMastered").textContent = mastery.mastered != null ? mastery.mastered : "-";
        $("statLearning").textContent = mastery.learning != null ? mastery.learning : "-";
        $("statDue").textContent = review.due_now != null ? review.due_now : "-";
        const trend = data.exam_trend || [];
        if (trend.length > 0) {
            const avg = trend.reduce((s, t) => s + (t.accuracy || 0), 0) / trend.length;
            $("statAccuracy").textContent = avg.toFixed(1) + "%";
        } else {
            $("statAccuracy").textContent = "-";
        }
    }

    // ==================== 章节热力图 ====================

    function heatColor(rate) {
        if (rate == null) return "var(--bg-3)";
        if (rate >= 80) return "rgba(52,199,123,1)";
        if (rate >= 60) return "rgba(52,199,123,0.8)";
        if (rate >= 40) return "rgba(52,199,123,0.55)";
        if (rate >= 20) return "rgba(52,199,123,0.35)";
        return "rgba(52,199,123,0.15)";
    }

    function renderHeatmap(sections) {
        const container = $("heatmap");
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

    // ==================== 各层掌握率 ====================

    function renderLayerMastery(layers) {
        const container = $("layerMastery");
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

    // ==================== 考试正确率趋势（SVG 折线） ====================

    function renderLineChart(trend) {
        const container = $("trendChart");
        if (trend.length < 1) {
            container.innerHTML = '<div class="quiz-empty"><p>暂无考试记录</p></div>';
            return;
        }

        const W = 800, H = 240, padL = 46, padR = 16, padT = 16, padB = 34;
        const innerW = W - padL - padR, innerH = H - padT - padB;
        const n = trend.length;

        const x = (i) => padL + (n === 1 ? innerW / 2 : (i / (n - 1)) * innerW);
        const y = (v) => padT + innerH - (Math.min(Math.max(v, 0), 100) / 100) * innerH;

        let svg = '<svg viewBox="0 0 ' + W + " " + H + '" xmlns="http://www.w3.org/2000/svg">';

        // 网格线 + Y 轴标签
        [0, 50, 100].forEach((v) => {
            svg += '<line class="grid-line" x1="' + padL + '" y1="' + y(v) + '" x2="' + (W - padR) + '" y2="' + y(v) + '" />';
            svg += '<text class="axis-text" x="' + (padL - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + v + "%</text>";
        });

        // 折线 + 数据点
        const pts = trend.map((t, i) => ({ x: x(i), y: y(t.accuracy), t }));
        const path = pts.map((p, i) => (i === 0 ? "M" : "L") + p.x.toFixed(1) + " " + p.y.toFixed(1)).join(" ");
        svg += '<path class="trend-line" d="' + path + '" />';

        pts.forEach((p, i) => {
            svg += '<circle class="trend-point" cx="' + p.x + '" cy="' + p.y + '" r="4" />';
            svg += '<text class="trend-label" x="' + p.x + '" y="' + (p.y - 8) + '" text-anchor="middle">' + p.t.accuracy + "%</text>";
            // X 轴日期标签（多于 6 个点时隔一个显示）
            if (n <= 6 || i % 2 === 0 || i === n - 1) {
                svg += '<text class="axis-text" x="' + p.x + '" y="' + (H - 10) + '" text-anchor="middle">' + esc(p.t.date) + "</text>";
            }
        });

        svg += "</svg>";
        container.innerHTML = svg;
    }

    // ==================== 复习日历（近 30 天） ====================

    function localDateKey(d) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, "0");
        const day = String(d.getDate()).padStart(2, "0");
        return y + "-" + m + "-" + day;
    }

    function calendarColor(count) {
        if (!count) return "var(--bg-3)";
        if (count <= 2) return "rgba(52,199,123,0.25)";
        if (count <= 5) return "rgba(52,199,123,0.5)";
        if (count <= 10) return "rgba(52,199,123,0.75)";
        return "var(--green)";
    }

    function renderCalendar(dailyTrend) {
        const container = $("calendar");
        const counts = {};
        (dailyTrend || []).forEach((d) => { counts[d.date] = d.count; });

        const days = [];
        const today = new Date();
        for (let i = 29; i >= 0; i--) {
            const d = new Date(today);
            d.setDate(today.getDate() - i);
            const key = localDateKey(d);
            days.push({ key, day: d.getDate(), count: counts[key] || 0 });
        }

        // 前置空位对齐星期（周一为一周开始）
        const firstWeekday = new Date(days[0].key + "T00:00:00").getDay();
        const lead = (firstWeekday + 6) % 7;

        let html = '<div class="calendar-weekdays">' +
            ["一", "二", "三", "四", "五", "六", "日"].map((w) => "<span>" + w + "</span>").join("") +
            "</div><div class=" + '"calendar-grid"' + ">";
        for (let i = 0; i < lead; i++) html += "<span></span>";
        days.forEach((d) => {
            const title = d.count > 0 ? d.key + " · 复习 " + d.count + " 次" : d.key + " · 无复习";
            html += '<div class="calendar-cell" title="' + title + '" style="background:' + calendarColor(d.count) + ';">' + d.day + "</div>";
        });
        html += "</div>";

        const total30 = days.reduce((s, d) => s + d.count, 0);
        html += '<div class="calendar-legend">近 30 天共复习 ' + total30 + " 次</div>";
        container.innerHTML = html;
    }

    // ==================== 薄弱点 Top10 ====================

    function renderWeakPoints(points) {
        const container = $("weakPoints");
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

    // ==================== 最近活动 ====================

    function renderActivity(list) {
        const container = $("activity");
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

    let currentReport = "";

    async function exportReport() {
        const btn = $("btnExportReport");
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 生成中...';
        const sid = getSessionId();
        try {
            const data = await apiPost("/api/dashboard/report/" + encodeURIComponent(sid));
            if (!data.report) {
                showToast("报告生成失败，请稍后重试", "error");
                return;
            }
            currentReport = data.report;
            $("reportContent").textContent = currentReport;
            $("reportModal").style.display = "flex";
        } catch (e) {
            console.error("生成报告失败", e);
            showToast("报告生成失败：" + e.message, "error");
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-wand-magic-sparkles"></i> 生成学习报告（LLM）';
        }
    }

    function closeReportModal() {
        $("reportModal").style.display = "none";
    }

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
        a.download = "学习报告_" + getSessionId() + ".md";
        a.click();
        URL.revokeObjectURL(a.href);
    }

    function printReport() {
        $("printReport").innerHTML = "<h3>学习报告</h3><pre>" + esc(currentReport) + "</pre>";
        closeReportModal();
        setTimeout(() => window.print(), 100);
    }

    // 点击遮罩关闭弹窗
    $("reportModal").addEventListener("click", (e) => {
        if (e.target === $("reportModal")) closeReportModal();
    });

    // 暴露给内联 onclick
    window.exportReport = exportReport;
    window.closeReportModal = closeReportModal;
    window.copyReport = copyReport;
    window.downloadReport = downloadReport;
    window.printReport = printReport;

    // ==================== 初始化 ====================
    loadOverview();
})();
