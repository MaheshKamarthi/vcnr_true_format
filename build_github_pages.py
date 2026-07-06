import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLAYER_DIR = ROOT / "web_player"
PUBLIC_VCNR_DIR = ROOT / "public_vcnr"
SITE_DIR = ROOT / "site"
SITE_MEDIA_DIR = SITE_DIR / "media"
SAMPLE_NAME = "sample.vcnr"
SAMPLE_CONFIG_FILE = SITE_DIR / "sample-config.json"


def copy_directory_contents(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def write_sample_config() -> None:
    sample_path = SITE_MEDIA_DIR / SAMPLE_NAME
    if sample_path.is_file():
        sample_url = f"media/{SAMPLE_NAME}"
    else:
        sample_url = ""
    SAMPLE_CONFIG_FILE.write_text(
        json.dumps({"sample": sample_url}, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if not PLAYER_DIR.is_dir():
        raise FileNotFoundError(f"Missing player directory: {PLAYER_DIR}")

    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)

    copy_directory_contents(PLAYER_DIR, SITE_DIR)

    if PUBLIC_VCNR_DIR.is_dir():
        copy_directory_contents(PUBLIC_VCNR_DIR, SITE_MEDIA_DIR)
    else:
        SITE_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    write_sample_config()
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print(f"Built GitHub Pages site in: {SITE_DIR}")
    print(f"Player entry point: {SITE_DIR / 'index.html'}")
    print(f"Public VCNR directory: {SITE_MEDIA_DIR}")


if __name__ == "__main__":
    main()
