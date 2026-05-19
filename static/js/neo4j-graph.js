/**
 * Neo4j风格的知识图谱可视化组件
 * 提供专业的图谱渲染、交互和动画效果
 */

class Neo4jGraph {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.container = d3.select(`#${containerId}`);

        // 获取容器尺寸，如果为0则使用默认值
        let width = this.container.node().clientWidth;
        let height = this.container.node().clientHeight;

        // 如果容器尺寸为0，尝试获取父容器尺寸或使用默认值
        if (width === 0 || height === 0) {
            const parent = this.container.node().parentElement;
            if (parent) {
                width = width || parent.clientWidth || 600;
                height = height || parent.clientHeight || 400;
            } else {
                width = width || 600;
                height = height || 400;
            }
            console.warn(`容器尺寸为0，使用默认值: ${width}x${height}`);
        }

        this.width = options.width || width;
        this.height = options.height || height;

        // 配置选项
        this.options = {
            nodeRadius: 25,
            linkDistance: 120,
            linkStrength: 0.3,
            chargeStrength: -500,
            gravityStrength: 0.05,
            fontSize: 12,
            strokeWidth: 2,
            ...options
        };

        // 颜色方案 - Neo4j风格
        this.colorScheme = {
            'Protocol': '#4A90E2',
            'Device': '#7B68EE',
            'Layer': '#48D1CC',
            'Concept': '#3CB371',
            'Entity': '#6B8E23',
            'Question': '#FFB347',
            'Answer': '#FF69B4',
            'Problem': '#FF6B6B',
            'Solution': '#4ECDC4',
            'default': '#95A5A6'
        };

        // 状态变量
        this.simulation = null;
        this.svg = null;
        this.g = null;
        this.nodes = [];
        this.links = [];
        this.currentTransform = d3.zoomIdentity;

        this.init();
    }
    
    init() {
        // 清空容器
        this.container.selectAll('*').remove();
        
        // 创建SVG
        this.svg = this.container
            .append('svg')
            .attr('width', this.width)
            .attr('height', this.height)
            .attr('viewBox', [0, 0, this.width, this.height])
            .style('background', 'radial-gradient(circle at center, #f8f9fa 0%, #e9ecef 100%)');
        
        // 定义渐变和滤镜
        this.defineGradientsAndFilters();
        
        // 创建主容器组
        this.g = this.svg.append('g');
        
        // 设置缩放行为
        const zoom = d3.zoom()
            .scaleExtent([0.1, 4])
            .on('zoom', (event) => {
                this.currentTransform = event.transform;
                this.g.attr('transform', event.transform);
            });
        
        this.svg.call(zoom);
        
        // 创建力导向模拟
        this.simulation = d3.forceSimulation()
            .force('link', d3.forceLink().id(d => d.id).distance(this.options.linkDistance).strength(this.options.linkStrength))
            .force('charge', d3.forceManyBody().strength(this.options.chargeStrength))
            .force('center', d3.forceCenter(this.width / 2, this.height / 2))
            .force('collision', d3.forceCollide().radius(this.options.nodeRadius * 2.5))
            .force('x', d3.forceX(this.width / 2).strength(this.options.gravityStrength))
            .force('y', d3.forceY(this.height / 2).strength(this.options.gravityStrength));
    }
    
    defineGradientsAndFilters() {
        const defs = this.svg.append('defs');

        // 定义节点渐变 - 为所有已知类型定义
        Object.entries(this.colorScheme).forEach(([type, color]) => {
            const gradient = defs.append('radialGradient')
                .attr('id', `gradient-${type}`)
                .attr('cx', '30%')
                .attr('cy', '30%');

            gradient.append('stop')
                .attr('offset', '0%')
                .attr('stop-color', this.lightenColor(color, 20))
                .attr('stop-opacity', 0.9);

            gradient.append('stop')
                .attr('offset', '100%')
                .attr('stop-color', color)
                .attr('stop-opacity', 1);
        });

        // 为 default 类型单独定义（确保存在）
        if (!this.colorScheme['default']) {
            const gradient = defs.append('radialGradient')
                .attr('id', 'gradient-default')
                .attr('cx', '30%')
                .attr('cy', '30%');

            gradient.append('stop')
                .attr('offset', '0%')
                .attr('stop-color', '#B0B0B0')
                .attr('stop-opacity', 0.9);

            gradient.append('stop')
                .attr('offset', '100%')
                .attr('stop-color', '#95A5A6')
                .attr('stop-opacity', 1);
        }

        // 定义阴影滤镜
        const filter = defs.append('filter')
            .attr('id', 'drop-shadow')
            .attr('height', '130%');

        filter.append('feGaussianBlur')
            .attr('in', 'SourceAlpha')
            .attr('stdDeviation', 3);

        filter.append('feOffset')
            .attr('dx', 2)
            .attr('dy', 2)
            .attr('result', 'offsetblur');

        filter.append('feComponentTransfer')
            .append('feFuncA')
            .attr('type', 'linear')
            .attr('slope', 0.3);

        filter.append('feMerge')
            .append('feMergeNode');

        filter.append('feMerge')
            .append('feMergeNode')
            .attr('in', 'SourceGraphic');

        // 定义箭头标记
        const marker = defs.append('marker')
            .attr('id', 'arrowhead')
            .attr('viewBox', '-0 -5 10 10')
            .attr('refX', 20)
            .attr('refY', 0)
            .attr('orient', 'auto')
            .attr('markerWidth', 8)
            .attr('markerHeight', 8)
            .attr('xoverflow', 'visible');

        marker.append('svg:path')
            .attr('d', 'M 0,-5 L 10 ,0 L 0,5')
            .attr('fill', '#999')
            .style('stroke', 'none');
    }
    
    lightenColor(color, percent) {
        const num = parseInt(color.replace('#', ''), 16);
        const amt = Math.round(2.55 * percent);
        const R = (num >> 16) + amt;
        const G = (num >> 8 & 0x00FF) + amt;
        const B = (num & 0x0000FF) + amt;
        return '#' + (0x1000000 + (R < 255 ? R < 1 ? 0 : R : 255) * 0x10000 +
            (G < 255 ? G < 1 ? 0 : G : 255) * 0x100 +
            (B < 255 ? B < 1 ? 0 : B : 255))
            .toString(16).slice(1);
    }
    
    updateGraph(graphData) {
        if (!graphData || !graphData.nodes) {
            console.warn('图谱数据为空');
            return;
        }

        // 计算合适的初始分布半径
        const nodeCount = graphData.nodes.length;
        const minDimension = Math.min(this.width, this.height);
        const radius = Math.max(minDimension / 3, nodeCount * 20);

        // 处理节点数据 - 设置随机初始位置
        this.nodes = graphData.nodes.map((node, index) => {
            // 使用螺旋分布算法，避免节点初始位置重叠
            const angle = (index / nodeCount) * 2 * Math.PI * 3; // 多圈螺旋
            const r = (radius / nodeCount) * (index + 1) + this.options.nodeRadius * 2;
            return {
                id: node.name || `node-${index}`,
                name: node.name,
                type: node.type || 'default',
                description: node.description,
                // 设置螺旋分布的初始位置，加上随机偏移
                x: this.width / 2 + r * Math.cos(angle) + (Math.random() - 0.5) * 30,
                y: this.height / 2 + r * Math.sin(angle) + (Math.random() - 0.5) * 30,
                ...node
            };
        });

        // 处理关系数据
        this.links = [];
        if (graphData.relationships) {
            graphData.relationships.forEach(rel => {
                if (rel.start && rel.end) {
                    this.links.push({
                        source: rel.start.name || rel.start,
                        target: rel.end.name || rel.end,
                        type: rel.type || 'RELATED_TO',
                        ...rel
                    });
                }
            });
        }

        this.render();
    }
    
    render() {
        // 清除现有元素
        this.g.selectAll('*').remove();

        // 为未知节点类型动态创建渐变
        const unknownTypes = [...new Set(this.nodes.map(n => n.type))]
            .filter(type => !this.colorScheme[type]);

        unknownTypes.forEach(type => {
            const color = this.generateColor(type);
            this.colorScheme[type] = color;

            const defs = this.svg.select('defs');
            const gradient = defs.append('radialGradient')
                .attr('id', `gradient-${type}`)
                .attr('cx', '30%')
                .attr('cy', '30%');

            gradient.append('stop')
                .attr('offset', '0%')
                .attr('stop-color', this.lightenColor(color, 20))
                .attr('stop-opacity', 0.9);

            gradient.append('stop')
                .attr('offset', '100%')
                .attr('stop-color', color)
                .attr('stop-opacity', 1);
        });

        // 创建关系线组
        const linkGroup = this.g.append('g').attr('class', 'links');

        // 创建关系线
        const links = linkGroup.selectAll('.link')
            .data(this.links)
            .enter().append('line')
            .attr('class', 'link')
            .attr('stroke', '#999')
            .attr('stroke-opacity', 0.6)
            .attr('stroke-width', 2)
            .attr('marker-end', 'url(#arrowhead)');

        // 创建关系标签
        const linkLabels = linkGroup.selectAll('.link-label')
            .data(this.links)
            .enter().append('text')
            .attr('class', 'link-label')
            .attr('text-anchor', 'middle')
            .attr('dy', -5)
            .attr('font-size', '10px')
            .attr('fill', '#666')
            .text(d => d.type);

        // 创建节点组
        const nodeGroup = this.g.append('g').attr('class', 'nodes');

        // 创建节点容器
        const nodes = nodeGroup.selectAll('.node')
            .data(this.nodes)
            .enter().append('g')
            .attr('class', 'node')
            .call(this.createDragBehavior());

        // 添加节点圆圈
        nodes.append('circle')
            .attr('r', this.options.nodeRadius)
            .attr('fill', d => `url(#gradient-${d.type || 'default'})`)
            .attr('stroke', '#fff')
            .attr('stroke-width', this.options.strokeWidth)
            .attr('filter', 'url(#drop-shadow)')
            .on('click', (event, d) => this.showNodeDetails(event, d))
            .on('mouseover', (event, d) => this.highlightNode(d))
            .on('mouseout', () => this.unhighlightNodes());

        // 添加节点图标
        nodes.append('text')
            .attr('class', 'node-icon')
            .attr('text-anchor', 'middle')
            .attr('dy', '0.3em')
            .attr('font-size', '16px')
            .attr('fill', '#fff')
            .text(d => this.getNodeIcon(d.type));

        // 添加节点标签
        nodes.append('text')
            .attr('class', 'node-label')
            .attr('text-anchor', 'middle')
            .attr('dy', this.options.nodeRadius + 15)
            .attr('font-size', this.options.fontSize)
            .attr('fill', '#333')
            .attr('font-weight', '500')
            .text(d => this.truncateText(d.name, 12));

        // 更新力导向模拟
        this.simulation
            .nodes(this.nodes)
            .on('tick', () => this.ticked(links, linkLabels, nodes));

        this.simulation.force('link')
            .links(this.links);

        // 使用更高的初始alpha和更慢的衰减，让模拟有更多时间稳定
        this.simulation
            .alpha(1.5)
            .alphaDecay(0.01)
            .velocityDecay(0.3)
            .restart();
    }

    // 根据类型名称生成颜色
    generateColor(type) {
        let hash = 0;
        for (let i = 0; i < type.length; i++) {
            hash = type.charCodeAt(i) + ((hash << 5) - hash);
        }
        const h = Math.abs(hash) % 360;
        return `hsl(${h}, 60%, 55%)`;
    }
    
    createDragBehavior() {
        return d3.drag()
            .on('start', (event, d) => {
                // 开始拖拽时增加模拟alpha，但不要完全固定节点
                if (!event.active) this.simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
            })
            .on('drag', (event, d) => {
                // 拖拽时更新固定位置
                d.fx = event.x;
                d.fy = event.y;
            })
            .on('end', (event, d) => {
                // 拖拽结束后，保持节点在最终位置（不释放固定）
                if (!event.active) this.simulation.alphaTarget(0);
                // 保持节点在拖拽后的位置
                d.fx = d.x;
                d.fy = d.y;
            });
    }
    
    ticked(links, linkLabels, nodes) {
        links
            .attr('x1', d => d.source.x)
            .attr('y1', d => d.source.y)
            .attr('x2', d => d.target.x)
            .attr('y2', d => d.target.y);
        
        linkLabels
            .attr('x', d => (d.source.x + d.target.x) / 2)
            .attr('y', d => (d.source.y + d.target.y) / 2);
        
        nodes.attr('transform', d => `translate(${d.x},${d.y})`);
    }
    
    getNodeIcon(type) {
        const icons = {
            'Protocol': '⚡',
            'Device': '🖥️',
            'Layer': '📊',
            'Concept': '💡',
            'Entity': '📍',
            'Question': '❓',
            'Answer': '✅',
            'Problem': '⚠️',
            'Solution': '🔧'
        };
        return icons[type] || '📍';
    }
    
    truncateText(text, maxLength) {
        return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
    }
    
    highlightNode(node) {
        // 高亮相关节点和关系
        d3.selectAll('.node circle')
            .style('opacity', d => d === node ? 1 : 0.3);
        
        d3.selectAll('.link')
            .style('opacity', d => d.source === node || d.target === node ? 0.8 : 0.1);
    }
    
    unhighlightNodes() {
        d3.selectAll('.node circle').style('opacity', 1);
        d3.selectAll('.link').style('opacity', 0.6);
    }
    
    showNodeDetails(event, node) {
        // 创建详细信息面板
        const details = this.createNodeDetails(node);
        
        // 显示在合适的位置
        const tooltip = d3.select('body').append('div')
            .attr('class', 'neo4j-tooltip')
            .style('position', 'absolute')
            .style('background', 'white')
            .style('border', '1px solid #ddd')
            .style('border-radius', '8px')
            .style('padding', '15px')
            .style('box-shadow', '0 4px 20px rgba(0,0,0,0.15)')
            .style('max-width', '350px')
            .style('z-index', '1000')
            .style('font-family', 'Arial, sans-serif')
            .html(details);
        
        // 设置位置
        const tooltipNode = tooltip.node();
        const tooltipRect = tooltipNode.getBoundingClientRect();
        const containerRect = this.container.node().getBoundingClientRect();
        
        let left = event.pageX + 10;
        let top = event.pageY - tooltipRect.height / 2;
        
        // 确保不超出视窗
        if (left + tooltipRect.width > window.innerWidth) {
            left = event.pageX - tooltipRect.width - 10;
        }
        if (top < 0) {
            top = 10;
        }
        if (top + tooltipRect.height > window.innerHeight) {
            top = window.innerHeight - tooltipRect.height - 10;
        }
        
        tooltip.style('left', left + 'px')
            .style('top', top + 'px');
        
        // 点击其他地方关闭
        const closeTooltip = () => {
            tooltip.remove();
            document.removeEventListener('click', closeTooltip);
        };
        
        setTimeout(() => {
            document.addEventListener('click', closeTooltip);
        }, 100);
    }
    
    createNodeDetails(node) {
        let details = `
            <div style="border-bottom: 2px solid ${this.colorScheme[node.type] || this.colorScheme.default}; margin-bottom: 10px; padding-bottom: 8px;">
                <h3 style="margin: 0; color: ${this.colorScheme[node.type] || this.colorScheme.default}; font-size: 16px;">
                    ${this.getNodeIcon(node.type)} ${node.name}
                </h3>
                <span style="background: ${this.colorScheme[node.type] || this.colorScheme.default}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">
                    ${node.type}
                </span>
            </div>
        `;
        
        if (node.description) {
            details += `<p style="margin: 8px 0; color: #666; font-size: 13px;">${node.description}</p>`;
        }
        
        // 添加其他属性
        Object.keys(node).forEach(key => {
            if (!['id', 'name', 'type', 'description', 'x', 'y', 'fx', 'fy', 'vx', 'vy', 'index'].includes(key) && node[key]) {
                const value = Array.isArray(node[key]) ? node[key].join(', ') : node[key];
                details += `
                    <div style="margin: 5px 0;">
                        <strong style="color: #333; font-size: 12px;">${key}:</strong>
                        <span style="color: #666; font-size: 12px;">${value}</span>
                    </div>
                `;
            }
        });
        
        // 添加统计信息
        const connectedNodes = this.links.filter(l => l.source.id === node.id || l.target.id === node.id).length;
        details += `
            <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid #eee; font-size: 11px; color: #888;">
                连接数: ${connectedNodes}
                <button onclick="window._expandNodeNeighbors('${node.name}')" style="margin-left: 8px; background: #4A90E2; color: white; border: none; padding: 3px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;">
                    展开邻居
                </button>
            </div>
        `;
        
        return details;
    }
    
    // 公共方法
    resize(width, height) {
        this.width = width;
        this.height = height;
        this.svg.attr('width', width).attr('height', height);
        this.simulation.force('center', d3.forceCenter(width / 2, height / 2));
        this.simulation.alpha(0.3).restart();
    }
    
    resetZoom() {
        this.svg.transition()
            .duration(750)
            .call(d3.zoom().transform, d3.zoomIdentity);
    }

    // 重置节点布局 - 重新分布所有节点
    resetLayout() {
        const nodeCount = this.nodes.length;
        if (nodeCount === 0) return;

        const minDimension = Math.min(this.width, this.height);
        const radius = Math.max(minDimension / 3, nodeCount * 20);

        // 重新设置节点位置
        this.nodes.forEach((node, index) => {
            const angle = (index / nodeCount) * 2 * Math.PI * 3;
            const r = (radius / nodeCount) * (index + 1) + this.options.nodeRadius * 2;
            node.x = this.width / 2 + r * Math.cos(angle) + (Math.random() - 0.5) * 30;
            node.y = this.height / 2 + r * Math.sin(angle) + (Math.random() - 0.5) * 30;
            node.fx = null;
            node.fy = null;
        });

        // 重新启动模拟
        this.simulation
            .alpha(1.5)
            .alphaDecay(0.01)
            .velocityDecay(0.3)
            .restart();
    }
    
    zoomToFit() {
        try {
            const gNode = this.g.node();
            if (!gNode) return;

            const bounds = gNode.getBBox();
            const fullWidth = this.width;
            const fullHeight = this.height;
            const width = bounds.width;
            const height = bounds.height;
            const midX = bounds.x + width / 2;
            const midY = bounds.y + height / 2;

            if (width === 0 || height === 0 || isNaN(width) || isNaN(height)) {
                console.warn('无法计算图谱边界，使用默认视图');
                return;
            }

            const scale = 0.8 / Math.max(width / fullWidth, height / fullHeight);
            const translate = [fullWidth / 2 - scale * midX, fullHeight / 2 - scale * midY];

            this.svg.transition()
                .duration(750)
                .call(d3.zoom().transform, d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale));
        } catch (e) {
            console.warn('zoomToFit 失败:', e);
        }
    }
    
    exportAsSVG() {
        const svgData = this.svg.node().outerHTML;
        const blob = new Blob([svgData], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `neo4j-graph-${Date.now()}.svg`;
        a.click();
        URL.revokeObjectURL(url);
    }
    
    destroy() {
        if (this.simulation) {
            this.simulation.stop();
        }
        this.container.selectAll('*').remove();
    }
}

// 导出类供其他模块使用
window.Neo4jGraph = Neo4jGraph;