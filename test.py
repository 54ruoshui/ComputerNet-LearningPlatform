import argparse

# 1. 创建解析器对象
parser = argparse.ArgumentParser(description="这是一个测试脚本")

# 2. 添加参数
parser.add_argument("--num", type=int, help="输入一个数字")
parser.add_argument("--name", type=str, default="张三", help="输入名字，可选参数")

# 3. 解析命令行参数，得到对象
args = parser.parse_args()

# 使用参数
print(args.num)
print(args.name)
