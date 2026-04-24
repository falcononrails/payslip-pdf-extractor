import multiprocessing
import sys

from payslip_extractor.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())