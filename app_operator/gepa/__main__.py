"""Allow running GEPA via: python -m app_operator.gepa"""

import sys

from app_operator.gepa.cli import main

sys.exit(main())
