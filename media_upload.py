"""Adding uploaded images, GIFs and .zip files of them to a slideshow."""

import io
import re
import zipfile
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
ANIMATED_EXT = {".gif"}
# still images with names like these are covers/overviews when they come alongside animations
COVER_WORDS = re.compile(r"cover|thumb|preview|poster|contact[-_ ]?sheet|folder|banner", re.I)
MAX_FILES = 1000
MAX_UNZIPPED = 500 * 1024 * 1024


def natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _safe_name(name):
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9 _.()-]", "_", stem).strip(" .")[:80] or "image"
    return stem + ext


def _probe(data):
    """(kind, frame_count, length_ms) for image bytes, or raise ValueError."""
    from PIL import Image, UnidentifiedImageError
    try:
        img = Image.open(io.BytesIO(data))
        n = getattr(img, "n_frames", 1)
        length = 0
        if n > 1:
            for i in range(min(n, 500)):
                img.seek(i)
                d = img.info.get("duration") or 100
                length += d if d > 10 else 100
        else:
            img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError(f"not an image Pillow can read ({e})") from None
    return ("gif" if n > 1 else "image"), n, length


def add_file(store, gid, name, data):
    kind, frames, length = _probe(data)
    folder = store.gallery_dir(gid)
    fname = _safe_name(name)
    base, ext, i = Path(fname).stem, Path(fname).suffix, 2
    while (folder / fname).exists():
        fname, i = f"{base}-{i}{ext}", i + 1
    (folder / fname).write_bytes(data)
    item = {"file": fname, "name": Path(name).stem, "kind": kind, "frames": frames, "length_ms": length}
    store.gallery(gid)["items"].append(item)
    return item


def is_cover(name, animated_stems):
    """A still image that's really a cover: same name as an animation, or named like one."""
    stem = Path(name).stem.lower()
    return stem in animated_stems or bool(COVER_WORDS.search(stem))


def add_upload(store, gid, name, data, skip_covers=True):
    """A single image/GIF, or a .zip of them (unpacked; non-images and folders are ignored).
    With skip_covers, a zip's still images that are just covers for its GIFs are left out."""
    ext = Path(name).suffix.lower()
    added, skipped = [], []
    if ext == ".zip":
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("that .zip file is damaged or not a zip") from None
        infos = [i for i in z.infolist()
                 if not i.is_dir() and "__MACOSX" not in i.filename
                 and not Path(i.filename).name.startswith(".")
                 and Path(i.filename).suffix.lower() in IMAGE_EXT]
        if not infos:
            raise ValueError("no images found in that .zip")
        animated = {Path(i.filename).stem.lower() for i in infos if Path(i.filename).suffix.lower() in ANIMATED_EXT}
        if skip_covers and animated:
            covers = [i for i in infos if Path(i.filename).suffix.lower() not in ANIMATED_EXT
                      and is_cover(i.filename, animated)]
            if covers:
                skipped.append(f"{len(covers)} cover/preview image{'s' if len(covers) != 1 else ''} "
                               f"(e.g. {Path(covers[0].filename).name})")
                infos = [i for i in infos if i not in covers]
        if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
            raise ValueError("that .zip is too big (max 1000 images / 500 MB unpacked)")
        for info in sorted(infos, key=lambda i: natural_key(i.filename)):
            try:
                # only the file's own name is used, so paths inside the zip can't escape the folder
                added.append(add_file(store, gid, Path(info.filename).name, z.read(info)))
            except ValueError as e:
                skipped.append(f"{Path(info.filename).name}: {e}")
    elif ext in IMAGE_EXT:
        added.append(add_file(store, gid, name, data))
    else:
        raise ValueError("upload images (png, jpg, gif, webp, bmp) or a .zip of them")
    store.save_galleries()
    return added, skipped


def remove_covers(store, gid):
    """Drop still images that are covers for the slideshow's GIFs. Returns how many were removed."""
    g = store.gallery(gid)
    animated = {i["name"].lower() for i in g["items"] if i["kind"] == "gif"}
    if not animated:
        return 0
    keep, removed = [], 0
    for item in g["items"]:
        if item["kind"] != "gif" and is_cover(item["name"], animated):
            (store.gallery_dir(gid) / item["file"]).unlink(missing_ok=True)
            removed += 1
        else:
            keep.append(item)
    g["items"] = keep
    store.save_galleries()
    return removed
