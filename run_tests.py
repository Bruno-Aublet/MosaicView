"""Lance toute la suite de tests. Ouvrir ce fichier dans VSCode et cliquer sur ▶️ Run."""
import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(["tests", "-v", "--tb=short"]))
