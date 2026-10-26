/**
 * 复习调度前端逻辑
 * - 统计面板
 * - 到期/即将到期复习队列
 * - 手动记录复习
 * - 30 天趋势图 + 质量分布
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

    // 无本地会话时向服务端申请一个；失败则保持 "default" 回落（离线韧性）
    async function ensureSession() {
        if (localStorage.getItem("graphrag_session_id")) return;
        try {
            const res = await fetch("/api/sessions/new", { method: "POST" });
            const data = await res.json();
            if (data.session_id) localStorage.setItem("graphrag_session_id", data.session_id);
        } catch (e) { /* 静默 */ }
    }

    // --- Toast ---
    function showToast(msg, type) {
        const toast = $("toast");
        toast.textContent = msg;
        toast.className = "quiz-toast " + type + " show";
        setTimeout(() => { toast.className = "quiz-toast"; }, 3000);
    }

    // --- 加载统计 ---
    async function loadStats() {
        const sid = getSessionId();
        try {
            const stats = await apiGet("/api/review/stats/" + encodeURIComponent(sid));
            $("statTotal").textContent = stats.total_entities || 0;
            $("statRetention").textContent =
                stats.total_entities > 0 ? Math.round((stats.avg_retention || 0) * 100) + "%" : "-";
            $("statDue").textContent = stats.due_now || 0;
            $("statToday").textContent = stats.today_reviews || 0;

            // 回忆成功率（近 7 天优先）+ 趋势
            const sr = stats.success_rate_7d != null
                ? stats.success_rate_7d
                : stats.success_rate || 0;
            $("statSuccess").textContent = stats.total_reviews > 0
                ? Math.round(sr * 100) + "%" : "-";
            renderSuccessTrend(stats.success_rate_7d, stats.success_rate_prev_7d);

            // 平均记忆半衰期
            $("statHalfLife").textContent = stats.total_entities > 0
                ? (stats.avg_half_life_days || 0) + " 天" : "-";

            // 趋势图
            if (stats.daily_trend && stats.daily_trend.length > 0) {
                renderTrend(stats.daily_trend);
                $("trendSection").style.display = "block";
            }

            // 质量分布
            if (stats.quality_distribution && Object.keys(stats.quality_distribution).length > 0) {
                renderQualityDist(stats.quality_distribution, stats.total_reviews);
                $("qualitySection").style.display = "block";
            }

            // 记忆强度分布
            if (stats.retention_distribution) {
                const total = Object.values(stats.retention_distribution).reduce((a, b) => a + b, 0);
                if (total > 0) {
                    renderRetentionDist(stats.retention_distribution, total);
                    $("retentionDistSection").style.display = "block";
                }
            }

            // 未来 7 天复习负荷
            if (stats.forecast_7d && stats.forecast_7d.length > 0) {
                renderForecast(stats.forecast_7d);
                $("forecastSection").style.display = "block";
            }
        } catch (e) {
            console.error("加载统计失败:", e);
        }
    }

    function renderSuccessTrend(cur, prev) {
        const el = $("statSuccessTrend");
        if (cur == null || prev == null || prev === 0) {
            el.style.display = "none";
            return;
        }
        const deltaPt = ((cur - prev) * 100).toFixed(1);
        el.style.display = "block";
        if (deltaPt >= 0) {
            el.className = "stat-trend up";
            el.textContent = "▲ +" + deltaPt + "pt vs 前 7 天";
        } else {
            el.className = "stat-trend down";
            el.textContent = "▼ " + deltaPt + "pt vs 前 7 天";
        }
    }

    function renderTrend(dailyTrend) {
        const maxCount = Math.max(1, ...dailyTrend.map((d) => d.count));
        let html = "";
        for (const day of dailyTrend) {
            const h = Math.max(2, Math.round((day.count / maxCount) * 110));
            const dateLabel = day.date.slice(5); // MM-DD
            html += `<div class="trend-bar-wrap">
                <div class="trend-bar" style="height:${h}px;" title="${day.date}: ${day.count}次"></div>
                <span class="trend-date">${dateLabel}</span>
            </div>`;
        }
        $("trendChart").innerHTML = html;
    }

    function renderQualityDist(dist, total) {
        let html = "";
        for (let q = 5; q >= 0; q--) {
            const cnt = dist[String(q)] || 0;
            const pct = total > 0 ? Math.round((cnt / total) * 100) : 0;
            const labels = ["完全遗忘", "有些印象", "记得部分", "基本记得", "回忆正确", "熟练掌握"];
            html += `<div class="quality-row">
                <span class="quality-label">Q${q} ${labels[q]}</span>
                <div class="quality-bar-wrap">
                    <div class="quality-bar-fill q${q}" style="width:${Math.max(pct, 2)}%">${pct > 8 ? cnt : ""}</div>
                </div>
            </div>`;
        }
        $("qualityBars").innerHTML = html;
    }

    function renderRetentionDist(dist, total) {
        const buckets = [
            { label: "牢固", color: "#34c77b" },
            { label: "正常", color: "#3b82f6" },
            { label: "薄弱", color: "#f59e0b" },
            { label: "需复习", color: "#ef4444" },
        ];
        let html = "";
        for (const b of buckets) {
            const cnt = dist[b.label] || 0;
            const pct = total > 0 ? Math.round((cnt / total) * 100) : 0;
            html += `<div class="quality-row">
                <span class="quality-label">${b.label}</span>
                <div class="quality-bar-wrap">
                    <div class="quality-bar-fill" style="width:${Math.max(pct, 2)}%;background:${b.color};">${pct > 8 ? cnt : ""}</div>
                </div>
            </div>`;
        }
        $("retentionDistBars").innerHTML = html;
    }

    function renderForecast(forecast) {
        const maxCount = Math.max(1, ...forecast.map((f) => f.count));
        let html = "";
        for (const f of forecast) {
            const h = Math.max(2, Math.round((f.count / maxCount) * 110));
            const dateLabel = f.is_today ? "今天" : f.date.slice(5); // MM-DD
            html += `<div class="trend-bar-wrap">
                <div class="trend-bar" style="height:${h}px;${f.is_today ? "background:var(--accent);" : ""}" title="${f.date}: ${f.count} 个待复习"></div>
                <span class="trend-date">${dateLabel}</span>
            </div>`;
        }
        $("forecastChart").innerHTML = html;
    }

    // --- 加载到期复习 ---
    async function loadDueReviews() {
        const sid = getSessionId();
        const lookahead = parseInt($("lookaheadSelect").value) || 48;
        try {
            const data = await apiGet(
                "/api/review/due/" + encodeURIComponent(sid) + "?lookahead=" + lookahead
            );

            const container = $("dueList");
            if (!data || data.length === 0) {
                container.innerHTML = `<div class="quiz-empty" id="dueEmpty">
                    <i class="fas fa-check-circle" style="font-size:2rem;color:var(--green);margin-bottom:12px;"></i>
                    <p>暂无到期复习内容</p>
                </div>`;
                return;
            }

            let html = "";
            for (const item of data) {
                const color = item.color;
                const retentionPct = Math.round(item.retention * 100);
                const dueText = item.is_due
                    ? `<span style="color:#ef4444;">已过期 ${Math.round(-item.hours_until_due)} 小时</span>`
                    : `<span>${Math.round(item.hours_until_due)} 小时后到期</span>`;
                const labelTag = `<span class="status-tag ${color}">${item.label}</span>`;

                html += `
                <div class="review-item" data-entity="${item.entity_name}">
                    <div class="urgency-dot ${color}"></div>
                    <div class="review-item-body">
                        <div class="review-item-name">${item.entity_name}</div>
                        <div class="review-item-meta">
                            ${labelTag}
                            <span><i class="fas fa-rotate"></i> 已复习 ${item.review_count} 次</span>
                            ${dueText}
                        </div>
                    </div>
                    <div class="retention-bar-wrap">
                        <div class="retention-bar-fill ${color}" style="width:${retentionPct}%"></div>
                    </div>
                    <span class="retention-pct ${color}">${retentionPct}%</span>
                    <div class="review-item-actions">
                        <button class="btn-review-done quality-high" onclick="quickReview('${item.entity_name}', 4)" title="记得">
                            <i class="fas fa-check"></i>
                        </button>
                        <button class="btn-review-done quality-mid" onclick="quickReview('${item.entity_name}', 2)" title="模糊">
                            <i class="fas fa-question"></i>
                        </button>
                        <button class="btn-review-done quality-low" onclick="quickReview('${item.entity_name}', 0)" title="遗忘">
                            <i class="fas fa-xmark"></i>
                        </button>
                    </div>
                </div>`;
            }
            container.innerHTML = html;
        } catch (e) {
            console.error("加载复习队列失败:", e);
            $("dueList").innerHTML = `<div class="quiz-empty"><p>加载失败: ${e.message}</p></div>`;
        }
    }

    // --- 快速标记复习 ---
    window.quickReview = async function (entityName, quality) {
        const sid = getSessionId();
        try {
            const res = await apiPost("/api/review/record", {
                session_id: sid,
                entity_name: entityName,
                quality: quality,
                event_type: "manual",
            });
            let msg = `已记录: ${entityName} (Q${quality})`;
            if (res && res.next_review_at) {
                const next = new Date(res.next_review_at).toLocaleString("zh-CN", {
                    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
                });
                msg += `，下次复习 ${next}`;
            }
            showToast(msg, "success");
            await loadDueReviews();
            await loadStats();
        } catch (e) {
            showToast("记录失败: " + e.message, "error");
        }
    };

    // --- 手动记录复习 ---
    window.submitManualReview = async function () {
        const entityName = $("manualEntity").value.trim();
        const quality = parseInt($("manualQuality").value);
        if (!entityName) {
            showToast("请输入知识点名称", "error");
            return;
        }
        await quickReview(entityName, quality);
        $("manualEntity").value = "";
    };

    // --- 展开全部状态 ---
    window.toggleAllStates = async function () {
        const container = $("allStates");
        const label = $("expandLabel");
        const icon = $("expandIcon");
        const btn = document.querySelector(".btn-expand");

        if (container.style.display === "none") {
            container.style.display = "block";
            label.textContent = "收起";
            btn.classList.add("open");
            await loadAllStates();
        } else {
            container.style.display = "none";
            label.textContent = "展开";
            btn.classList.remove("open");
        }
    };

    async function loadAllStates() {
        const sid = getSessionId();
        try {
            const data = await apiGet("/api/review/state/" + encodeURIComponent(sid));
            if (!data || data.length === 0) {
                $("statesEmpty").style.display = "block";
                return;
            }
            $("statesEmpty").style.display = "none";

            let html = `<table class="review-table">
                <thead><tr>
                    <th>知识点</th><th>状态</th><th>保留率</th><th>复习次数</th><th>下次复习</th><th>半衰期</th>
                </tr></thead><tbody>`;
            for (const item of data) {
                const color = item.color;
                const retentionPct = Math.round(item.retention * 100);
                const nextReview = item.next_review_at
                    ? new Date(item.next_review_at).toLocaleDateString("zh-CN")
                    : "-";
                const halfLifeDays = Math.round(item.half_life / 24);
                html += `<tr>
                    <td>${item.entity_name}</td>
                    <td><span class="status-tag ${color}">${item.label}</span></td>
                    <td><span style="color:var(--${color === 'red' ? 'red' : color === 'orange' ? 'amber' : color === 'blue' ? 'accent' : 'green'});">${retentionPct}%</span></td>
                    <td>${item.review_count}</td>
                    <td>${nextReview}</td>
                    <td>${halfLifeDays} 天</td>
                </tr>`;
            }
            html += "</tbody></table>";
            $("allStates").innerHTML = html;
        } catch (e) {
            console.error("加载全部状态失败:", e);
        }
    }

    // --- 自动补全实体列表 ---
    async function loadEntitySuggestions() {
        try {
            const data = await apiGet("/api/entities");
            const names = [];
            for (const layer of Object.values(data)) {
                if (Array.isArray(layer)) {
                    for (const entity of layer) {
                        if (entity.name) names.push(entity.name);
                    }
                }
            }
            const datalist = $("entitySuggestions");
            datalist.innerHTML = names.map((n) => `<option value="${n}">`).join("");
        } catch (e) {
            // 静默失败，不影响手动输入
        }
    }

    // --- 初始化 ---
    ensureSession().then(() => {
        loadStats();
        loadDueReviews();
        loadEntitySuggestions();
    });
})();
