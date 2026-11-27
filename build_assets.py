"""原作 project.json からマップ素材 (コスチュームSVG) を抽出して PNG 化する。

TACT-AIM 本家 (831274817) と blood (1297721083) の Pen/All コスチュームのうち
マップ描画に使うものを Scratch のアセット CDN から取得し、cairosvg で
4倍解像度の PNG にして assets/<project_id>/ へ保存する。再実行可能 (開発用)。
実行には cairosvg が必要 (Bot 本体の動作には不要)。
"""
import io
import json
import re
import urllib.request
from pathlib import Path

import cairosvg

ROOT = Path(__file__).parent
PROJECTS = {"831274817": "original", "1297721083": "blood"}

# コスチューム名 → ファイルキー
COSTUMES = {
    "Wall": "wall", "Bush": "bush", "Spike wall": "spike_wall",
    "Grid wall": "grid_wall", "Mud": "mud", "Shield": "shield",
    "Flash wall0": "flash_wall0", "Flash wall1": "flash_wall1",
    "Heal wall0": "heal_wall0", "Heal wall1": "heal_wall1",
    "Laser wall0": "laser_wall0", "Laser wall1": "laser_wall1",
    "Warp floor0": "warp_floor0", "Warp floor1": "warp_floor1",
    "Energy wall0": "energy_wall0", "Energy wall1": "energy_wall1",
    "Booster": "booster", "Skill spot0": "skill_spot0",
    "Skill spot1": "skill_spot1", "Team gem0": "team_gem0",
    "Team gem1": "team_gem1", "Warp1": "warp1", "Door": "door",
    "Map pointer": "map_pointer", "PVP manager": "pvp_manager",
    "Auto buffer": "auto_buffer", "Teaming machine": "teaming_machine",
    "Information base": "information_base", "Locker": "locker",
    "Minigame hub1": "minigame_hub1", "Minigame hub2": "minigame_hub2",
    "Telescope": "telescope", "Reporter": "reporter",
    "Computer": "computer", "Record player": "record_player",
}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "TACT-AIM-BOT asset build"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def project_json(pid: str) -> dict:
    meta = json.loads(fetch(f"https://api.scratch.mit.edu/projects/{pid}"))
    return json.loads(fetch(f"https://projects.scratch.mit.edu/{pid}?token={meta['project_token']}"))


def svg_size(svg: bytes) -> tuple[float, float]:
    text = svg.decode("utf-8", "ignore")
    m = re.search(r'viewBox="([\d.\-\s]+)"', text)
    if m:
        parts = [float(x) for x in m.group(1).split()]
        return parts[2], parts[3]
    w = re.search(r'width="([\d.]+)', text)
    h = re.search(r'height="([\d.]+)', text)
    return float(w.group(1)), float(h.group(1))


def main():
    for pid in PROJECTS:
        proj = project_json(pid)
        pen = next(t for t in proj["targets"] if t["name"] == "Pen/All")
        by_name = {c["name"]: c for c in pen["costumes"]}
        out_dir = ROOT / "assets" / pid
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {}
        for name, key in COSTUMES.items():
            c = by_name.get(name)
            if c is None:
                print(f"{pid} {name}: missing, skip")
                continue
            svg = fetch(f"https://assets.scratch.mit.edu/internalapi/asset/{c['md5ext']}/get/")
            w, h = svg_size(svg)
            png = cairosvg.svg2png(bytestring=svg, output_width=round(w * 4), output_height=round(h * 4))
            (out_dir / f"{key}.png").write_bytes(png)
            manifest[key] = [round(w, 1), round(h, 1)]
            print(f"{pid} {key}: {w:.0f}x{h:.0f} units -> {len(png)} bytes")
        (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
