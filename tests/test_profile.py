from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]


def test_canonical_profile_preserves_cv_titles_dates_and_metrics() -> None:
    profile = yaml.safe_load((ROOT / "data" / "profile.yaml").read_text(encoding="utf-8"))
    roles = {
        (role["organisation"], role["title"]): role for role in profile["experience"]
    }

    engineer = roles[("matriXploit Pvt. Ltd.", "Software Engineer")]
    support = roles[("matriXploit Pvt. Ltd.", "Technical Support Engineer")]
    internship = roles[("Physics Wallah Pvt. Ltd.", "Software Engineer Intern")]

    assert (engineer["start"], engineer["end"]) == ("Jun 2022", "Jul 2024")
    assert (support["start"], support["end"]) == ("Jan 2022", "Jun 2022")
    assert (internship["start"], internship["end"]) == ("Oct 2024", "Dec 2024")
    assert any("nearly 50%" in bullet for bullet in engineer["bullets"])
    assert any("about 30%" in bullet for bullet in engineer["bullets"])
    assert any("about 35%" in bullet for bullet in engineer["bullets"])
    assert profile["person"]["email"] == "prathameh7744yt@gmail.com"
