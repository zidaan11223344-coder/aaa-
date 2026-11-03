from pathlib import Path

from gift_assets import extend_gift_image_paths


def test_source_variants_are_added_only_to_registered_gifts(tmp_path: Path):
    base = tmp_path
    source = base / "assets" / "talkin2_gifts"
    source.mkdir(parents=True)
    for name in (
        "gift_01_1.jpg", "gift_01_2.png", "gift_01_3.jpg",
        "gift_14_1.jpg", "gift_15_coffee.jpg", "gift_40_love_hamper.jpg",
    ):
        (source / name).write_bytes(b"image")

    initial = {str(i): [f"assets/gift_{i:02d}_1.png"] for i in range(1, 15)}
    merged = extend_gift_image_paths(base, initial)

    assert merged["1"][0] == "assets/gift_01_1.png"
    assert {"assets/talkin2_gifts/gift_01_1.jpg", "assets/talkin2_gifts/gift_01_2.png", "assets/talkin2_gifts/gift_01_3.jpg"}.issubset(set(merged["1"]))
    assert "assets/talkin2_gifts/gift_14_1.jpg" in merged["14"]
    assert "15" not in merged and "40" not in merged


def test_real_bundled_images_are_complete_and_decodable():
    base = Path(__file__).resolve().parent
    merged = extend_gift_image_paths(base, {str(i): [] for i in range(1, 15)})
    for gift_id in range(1, 15):
        files = [base / path for path in merged[str(gift_id)] if "talkin2_gifts" in path]
        assert len(files) >= 3, f"missing source variants for gift {gift_id}"
        assert all(path.is_file() and path.stat().st_size > 0 for path in files)
