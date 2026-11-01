#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在职保存成功后，为当月 salary_realtime_draft 占位。默认不写库。

写库须 ALLOW_EMPLOYEE_DRAFT_WRITE=true 与 --apply。
不共用灌数闸 ALLOW_APPLY_REALTIME_DRAFT。2026-10 拒绝。

见 docs/契约_员工草案占位与底薪同步.md。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_employee_write


if __name__ == "__main__":
    sys.exit(draft_employee_write.main(mode="placeholder"))
