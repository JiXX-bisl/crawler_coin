import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

# 解决中文显示问题
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
plt.rcParams["axes.unicode_minus"] = False

# 原始数据
data = {
    "交易类型/AMM机制": 1815,
    "协议资料/交易所API": 1708,
    "主流币种/BTC/交易结构与UTXO": 1654,
    "交易类型/DEX兑换": 854,
    "风险监管/可疑交易识别": 728,
    "交易特征/MEV与套利": 543,
    "协议资料/跨链协议": 463,
    "主流币种/ETH/EVM与账户模型": 377,
    "协议资料/Bitcoin RPC": 340,
    "公链模型/Layer2与Rollup": 284,
    "交易特征/闪电贷": 263,
    "风险监管/反洗钱与合规": 259,
    "协议资料/智能合约接口": 229,
    "交易类型/DeFi借贷": 226,
    "主流币种/USDC/稳定币与跨链转账": 194,
    "交易特征/混币与隐私交易": 186,
    "风险监管/制裁地址": 159,
    "主流币种/DOGE/交易与网络资料": 143,
    "协议资料/Ethereum RPC": 131,
    "基础概念/智能合约": 89,
    "主流币种/SOL/账户与交易模型": 68,
    "主流币种/XRP/账本与支付模型": 67,
    "基础概念/区块链基础": 51,
    "公链模型/EVM": 37,
    "交易类型/NFT交易": 28,
    "基础概念/地址与钱包": 28,
    "协议资料/Solana RPC": 24,
    "公链模型/UTXO模型": 17,
    "交易类型/合约交互": 11,
    "其他/待归类知识": 7,
    "交易特征/地址聚类与资金流": 6,
    "交易类型/稳定币转账": 1,
    "基础概念/交易哈希与确认": 1,
    "基础概念/区块与节点": 1,
    "基础概念/手续费与Gas": 1
}

# 按数量降序排列
sorted_data = dict(sorted(data.items(), key=lambda x: x[1], reverse=True))

# 展示前 N 类，其余合并为“其他”
top_n = 12
top_items = list(sorted_data.items())[:top_n]
other_items = list(sorted_data.items())[top_n:]

plot_data = dict(top_items)
plot_data["其他类别合计"] = sum(value for _, value in other_items)

labels = list(plot_data.keys())
values = list(plot_data.values())

# 构造柔和颜色
def soften_color(color, factor=0.45):
    """
    factor 越大，颜色越接近白色，越柔和
    """
    rgb = mcolors.to_rgb(color)
    softened = tuple((1 - factor) * c + factor for c in rgb)
    return softened

base_cmap = plt.get_cmap("tab20")
colors = [soften_color(base_cmap(i), factor=0.38) for i in range(len(values))]

# 轻微分离扇区，增强 3D/层次感
explode = [0.035] * len(values)
explode[-1] = 0.06  # “其他类别合计”稍微突出一点

# 百分比显示控制：小于 2% 的不显示，避免拥挤
def autopct_func(pct):
    return f"{pct:.1f}%" if pct >= 2 else ""

# 绘制饼状图
plt.figure(figsize=(13, 10))

wedges, texts, autotexts = plt.pie(
    values,
    labels=None,                      # 长标签放到图例中，避免饼图拥挤
    autopct=autopct_func,
    startangle=90,
    pctdistance=0.72,
    colors=colors,
    explode=explode,
    shadow=True,                      # 伪 3D 阴影效果
    wedgeprops={
        "linewidth": 1.0,
        "edgecolor": "white"
    },
    textprops={
        "fontsize": 16,
        "color": "#333333"
    }
)

# 百分比文字样式
for autotext in autotexts:
    autotext.set_fontsize(16)
    autotext.set_color("#333333")
    autotext.set_weight("bold")

plt.title("虚拟货币知识库 Chunk 类型分布", fontsize=18, pad=20)
plt.axis("equal")

# 添加图例
plt.legend(
    wedges,
    labels,
    title="Chunk 类型",
    loc="center left",
    bbox_to_anchor=(1.02, 0.5),
    fontsize=16,
    title_fontsize=18,
    frameon=False
)

# 保存图片
plt.tight_layout()
plt.savefig(
    "crypto_knowledge_chunk_distribution_pie_3d_soft.png",
    dpi=300,
    bbox_inches="tight"
)

plt.show()