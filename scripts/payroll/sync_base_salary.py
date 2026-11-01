#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""底薪改完后，同步当月 salary_realtime_draft 的 base_amount 并重算应发。默认不写库。

见 docs/契约_员工草案占位与底薪同步.md。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_employee_write


if __name__ == "__main__":
    sys.exit(draft_employee_write.main(mode="sync"))
