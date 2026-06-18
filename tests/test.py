"""
关键词提取器功能测试
"""

import sys
import os
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
os.chdir(project_root)

from dotenv import load_dotenv
load_dotenv()

from src.keyword_extractor import KeywordExtractor

if __name__ == "__main__":
    extractor = KeywordExtractor()
    question = "请问TCP三次握手的过程是怎样的？DNS解析又是如何工作的？"
    keywords = extractor.extract(question)
    print("提取的关键词：", keywords)
