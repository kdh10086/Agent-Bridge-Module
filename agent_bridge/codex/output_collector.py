import time
from pathlib import Path


class AgentReportCollector:
    def __init__(self, report_path: Path):
        self.report_path = report_path

    def exists(self) -> bool:
        return self.report_path.exists()

    def is_stable(self, stable_seconds: int = 10) -> bool:
        if not self.report_path.exists():
            return False
        first = self.report_path.stat().st_mtime
        time.sleep(stable_seconds)
        second = self.report_path.stat().st_mtime
        return first == second

    def collect(self) -> str:
        if not self.report_path.exists():
            raise FileNotFoundError(f"Report not found: {self.report_path}")
        return self.report_path.read_text(encoding="utf-8")
