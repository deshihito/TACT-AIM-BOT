"""原作 project.json から素材 (コスチュームSVG/PNG) と武器データを抽出する。

TACT-AIM 本家 (831274817) と blood (1297721083) の Pen/All コスチュームのうち
マップ描画・観戦描画に使うものを Scratch のアセット CDN から取得し、PNG 化して
assets/<project_id>/ へ保存する。再実行可能 (開発用)。
実行には cairosvg と Pillow が必要 (Bot 本体の動作には不要)。

素材キーの規則 (ファイル名はキーの "/" を "__" に置換したもの):
- 地形: 従来キー (wall, bush, ...) … manifest 値は [幅, 高さ] (Scratch 単位)
- "C/<コスチューム名>": キャラ body/hand、特殊形態、Buff/Debuff
- "B/<種別名>": 弾コスチューム (元名は ":" + 種別名)
- "W/<武器名>" / "WL/<武器名>" / "WX/<武器名>" / "WA/<武器名>":
  武器の素・"<"・"×"・"@" コスチューム
- "F/<名前>": Stamp 命令のエフェクト (見た目コスチューム等)
  C/B/W/WL/WX/WA/F の manifest 値は [幅, 高さ, 回転中心X, 回転中心Y]

あわせて Stage の Item list / Bullet list / Skill を
assets/<project_id>/weapons.json へ書き出す (観戦ページの弾再現用)。
"""
import io
import json
import re
import urllib.request
from pathlib import Path

import cairosvg
from PIL import Image

from record import BLOOD_CHARACTERS, CHARACTERS

ROOT = Path(__file__).parent
PROJECTS = {
    "831274817": CHARACTERS,
    "1297721083": BLOOD_CHARACTERS,
}

# コスチューム名 → ファイルキー (地形分。従来どおり)
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
    "Team gem1": "team_gem1", "Warp1": "warp1", "Warp2": "warp2",
    "Warp light": "warp_light", "Door": "door",
    "Map pointer": "map_pointer", "PVP manager": "pvp_manager",
    "Auto buffer": "auto_buffer", "Teaming machine": "teaming_machine",
    "Information base": "information_base", "Locker": "locker",
    "Minigame hub1": "minigame_hub1", "Minigame hub2": "minigame_hub2",
    "Telescope": "telescope", "Reporter": "reporter",
    "Computer": "computer", "Record player": "record_player",
}

# キャラ以外で "C/" に入れるコスチューム (特殊形態・バフアイコン)
SPECIAL_COSTUMES = [
    "Shield Cannon body", "Shield Cannon", "Tank body",
    "Tank hand1", "Tank hand2", "Guardian body",
    "Guardian hand1", "Guardian hand2", "Buff", "Debuff",
]

# Bullet list 命令の引数個数 (Pen_All/combat.ts の Bullet action が正)。
# weapons.json のスクリプト分割検証に使う (web/app.js 側にも同じ表がある)。
COMMAND_ARITY = {
    "if": 3, "if end": 0, "else": 0, "draw bullet": 3, "distance if": 2,
    "cs val": 3, "sound": 1, "step": 1, "roll step": 1, "length": 8,
    "damage": 9, "sword damage": 4, "break wall": 6, "delete": 6,
    "stamp": 3, "team stamp": 3, "create": 7, "set length": 2, "bush": 1,
    "set position": 2, "round slide": 3, "draw laser": 4, "laser": 6,
    "put buff": 3, "put buff+": 5, "guard": 8, "screen stamp": 3,
    "draw circle": 3,
    "mine stamp": 2, "color": 1, "homing": 2, "team homing": 2,
    "roll homing": 2, "hit if": 0, "search first": 2, "skill point": 1,
    "shield": 1, "auto heal": 1, "about damage val": 0, "about drop item": 0,
    "about money": 0, "about tektite": 0, "about present": 0,
    "about shield cannon": 0, "about jj-100": 0, "about chat": 0,
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


def key_to_filename(key: str) -> str:
    return key.replace("/", "__") + ".png"


def save_costume(by_name: dict, name: str, key: str, out_dir: Path,
                 manifest: dict, with_center: bool) -> bool:
    """コスチュームを1枚保存し manifest へ登録する。無ければ False。"""
    c = by_name.get(name)
    if c is None:
        return False
    data = fetch(f"https://assets.scratch.mit.edu/internalapi/asset/{c['md5ext']}/get/")
    cx = float(c.get("rotationCenterX") or 0)
    cy = float(c.get("rotationCenterY") or 0)
    if c.get("dataFormat") == "svg":
        try:
            w, h = svg_size(data)
            png = cairosvg.svg2png(bytestring=data, output_width=round(w * 4), output_height=round(h * 4))
        except Exception as e:
            # サイズ未定義の SVG は1枚だけ諦めて全体は続ける
            print(f"  skip {name}: svg convert failed ({e})")
            return False
    elif c.get("dataFormat") == "png":
        res = float(c.get("bitmapResolution") or 1)
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.width / res, im.height / res
        cx, cy = cx / res, cy / res
        png = data
    else:
        print(f"  skip {name}: dataFormat={c.get('dataFormat')}")
        return False
    (out_dir / key_to_filename(key)).write_bytes(png)
    if with_center:
        manifest[key] = [round(w, 1), round(h, 1), round(cx, 1), round(cy, 1)]
    else:
        manifest[key] = [round(w, 1), round(h, 1)]
    return True


def stage_lists(proj: dict) -> dict:
    """Stage のリストを {正規化名: 値リスト} で返す。"""
    stage = next(t for t in proj["targets"] if t.get("isStage"))
    out = {}
    for _lid, entry in stage.get("lists", {}).items():
        name = entry[0].lstrip("#").strip().lower().replace("_", " ")
        out[name] = entry[1]
    return out


def split_scripts(bullet_list: list) -> dict:
    """Bullet list ("" 区切りのフラット列) を {種別名: トークン列} へ分割する。"""
    scripts = {}
    current = None
    for tok in bullet_list:
        if tok == "":
            current = None
        elif current is None:
            current = str(tok)
            scripts[current] = []
        else:
            scripts[current].append(tok)
    return scripts


def check_scripts(pid: str, scripts: dict) -> set:
    """スクリプトを命令表で走査し、末尾までぴったり読めるか検証する。

    戻り値は Stamp 系命令が参照するコスチューム名の集合。
    """
    stamp_targets = set()
    for name, tokens in scripts.items():
        i = 0
        while i < len(tokens):
            cmd = str(tokens[i]).lower()
            arity = COMMAND_ARITY.get(cmd)
            if arity is None:
                print(f"{pid} {name}: 未知の命令 {tokens[i]!r} (位置 {i})")
                break
            args = tokens[i + 1: i + 1 + arity]
            if len(args) < arity:
                print(f"{pid} {name}: 引数不足で途中終了 (位置 {i})")
                break
            if cmd in ("stamp", "team stamp", "mine stamp"):
                stamp_targets.add(str(args[0]))
            i += 1 + arity
        else:
            continue
        # while を break で抜けた場合は不整合として記録済み
    return stamp_targets


def chunk(values: list, width: int) -> list:
    return [values[i: i + width] for i in range(0, len(values) - width + 1, width)]


def main():
    for pid, roster in PROJECTS.items():
        proj = project_json(pid)
        pen = next(t for t in proj["targets"] if t["name"] == "Pen/All")
        by_name = {c["name"]: c for c in pen["costumes"]}
        lists = stage_lists(proj)
        out_dir = ROOT / "assets" / pid
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest = {}

        # 地形 (従来どおり)
        for name, key in COSTUMES.items():
            if not save_costume(by_name, name, key, out_dir, manifest, False):
                print(f"{pid} {name}: missing, skip")

        # キャラ (body / body+ / hand / hand+) と特殊形態
        char_names = []
        for char in roster:
            for suffix in (" body", " body+", " hand", " hand+"):
                char_names.append(char + suffix)
        char_names += SPECIAL_COSTUMES
        n_char = 0
        for name in char_names:
            if save_costume(by_name, name, "C/" + name, out_dir, manifest, True):
                n_char += 1
            elif name.endswith((" body", " body+")):
                print(f"{pid} {name}: missing, skip")

        # 弾コスチューム (":" 始まり)
        n_bullet = 0
        for name in by_name:
            if name.startswith(":"):
                if save_costume(by_name, name, "B/" + name[1:], out_dir, manifest, True):
                    n_bullet += 1

        # 武器データ (Item list / Bullet list / Skill)
        items = chunk(lists.get("item list", []), 14)
        scripts = split_scripts(lists.get("bullet list", []))
        skills = chunk(lists.get("skill", []), 15)
        stamp_targets = check_scripts(pid, scripts)

        # 武器コスチューム (素 / "<" / "×" / "@")
        n_weapon = 0
        for item in items:
            wname = str(item[0])
            for prefix, key_prefix in (("", "W/"), ("<", "WL/"), ("×", "WX/"), ("@", "WA/")):
                if save_costume(by_name, prefix + wname, key_prefix + wname,
                                out_dir, manifest, True):
                    n_weapon += 1

        # エフェクト (見た目コスチューム + Stamp 参照先 + キル表示)
        fx_names = {n for n in by_name if n.endswith("見た目")}
        fx_names |= stamp_targets | {"自キル", "他キル"}
        n_fx = 0
        for name in sorted(fx_names):
            if save_costume(by_name, name, "F/" + name, out_dir, manifest, True):
                n_fx += 1

        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        weapons = {"items": items, "scripts": scripts, "skills": skills}
        (out_dir / "weapons.json").write_text(
            json.dumps(weapons, ensure_ascii=False), encoding="utf-8")
        total = sum(f.stat().st_size for f in out_dir.glob("*.png"))
        print(f"{pid}: terrain {sum(1 for k in manifest if '/' not in k)} / "
              f"char {n_char} / bullet {n_bullet} / weapon {n_weapon} / fx {n_fx} / "
              f"items {len(items)} / scripts {len(scripts)} / skills {len(skills)} / "
              f"png total {total / 1e6:.1f} MB")
    print("done")


if __name__ == "__main__":
    main()
