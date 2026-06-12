from pathlib import Path
import sys
import logging
import os
if __name__ == "__main__":
    print("\n")

    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO,
                        filename=Path(__file__).parent / "test.log",
                        format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s')
    logger.debug("Debugging information")
    logger.info("Running tests...")
    
    
    
    
    print("\n")
    