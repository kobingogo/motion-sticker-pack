from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from inspect_sticker_sheet import detect_layout  # noqa: E402
from process_emoji_grid import (  # noqa: E402
    GridBoundaryError,
    assign_grid_components,
    estimate_global_translation,
    remove_edge_background,
    remove_interior_border_fragments,
    preserve_full_duration_indices,
    sample_full_duration_indices,
    select_short_delivery_indices,
    select_loop_indices,
    subject_alpha_damage,
    tile_bounds,
)


class GridDetectionTests(unittest.TestCase):
    def test_registration_estimate_remains_available_as_explicit_opt_in(self) -> None:
        baseline = [(10.0, 10.0), (30.0, 10.0), (50.0, 10.0)]
        current = [(12.0, 11.0), (32.0, 11.0), (52.0, 11.0)]
        shift_x, shift_y, report = estimate_global_translation(current, baseline, 40, 40)
        self.assertEqual((shift_x, shift_y), (-2, -1))
        self.assertTrue(report["applied"])

    def test_enclosed_exact_chroma_key_region_becomes_transparent(self) -> None:
        rgb = np.zeros((15, 15, 3), dtype=np.uint8)
        rgb[:, :] = (0, 255, 0)
        rgb[4:11, 4:11] = (255, 255, 255)
        rgb[6:9, 6:9] = (0, 255, 0)
        rgba = np.asarray(
            remove_edge_background(
                rgb,
                np.array([0, 255, 0], dtype=np.float32),
                remove_enclosed_key=True,
            )
        )
        self.assertEqual(int(rgba[7, 7, 3]), 0)
        self.assertEqual(int(rgba[5, 5, 3]), 255)

    def test_green_key_soft_edge_is_despilled(self) -> None:
        rgb = np.zeros((9, 9, 3), dtype=np.uint8)
        rgb[:, :] = (0, 255, 0)
        rgb[4, 4] = (255, 255, 255)
        rgb[3:6, 3:6] = np.maximum(rgb[3:6, 3:6], np.array([128, 0, 128], dtype=np.uint8))
        rgb[4, 4] = (255, 255, 255)
        image = remove_edge_background(
            rgb,
            np.array([0, 255, 0], dtype=np.float32),
            hard_tolerance=20.0,
            soft_tolerance=120.0,
        )
        rgba = np.asarray(image)
        foreground = rgba[:, :, 3] > 0
        self.assertTrue(np.any(foreground))
        rgb_int = rgba[:, :, :3].astype(np.int16)
        self.assertLessEqual(
            int(np.max(
                rgb_int[:, :, 1][foreground]
                - np.maximum(rgb_int[:, :, 0][foreground], rgb_int[:, :, 2][foreground])
            )),
            2,
        )

    def test_detects_actual_three_by_three(self) -> None:
        image = Image.new("RGBA", (600, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row in range(3):
            for column in range(3):
                x0 = column * 200 + 45
                y0 = row * 200 + 40
                draw.ellipse((x0, y0, x0 + 110, y0 + 120), fill=(80, 170, 240, 255))
        report = detect_layout(image, [(3, 3), (4, 3), (3, 4), (4, 4)])
        self.assertEqual(report["detected_layout"]["columns"], 3)
        self.assertEqual(report["detected_layout"]["rows"], 3)
        self.assertEqual(report["detected_layout"]["count"], 9)

    def test_detects_actual_four_by_three_instead_of_requested_three_by_three(self) -> None:
        image = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for row in range(3):
            for column in range(4):
                x0 = column * 200 + 45
                y0 = row * 200 + 35
                draw.rounded_rectangle((x0, y0, x0 + 110, y0 + 130), radius=25, fill=(240, 80, 120, 255))
        report = detect_layout(image, [(3, 3), (4, 3), (3, 4), (4, 4)], requested=(3, 3))
        self.assertEqual(report["detected_layout"]["columns"], 4)
        self.assertEqual(report["detected_layout"]["rows"], 3)
        self.assertEqual(report["detected_layout"]["count"], 12)

    def test_odd_dimension_bounds_cover_every_pixel_once(self) -> None:
        bounds = [tile_bounds(101, index, 4) for index in range(4)]
        self.assertEqual(bounds[0][0], 0)
        self.assertEqual(bounds[-1][1], 101)
        self.assertEqual([left for left, _ in bounds[1:]], [right for _, right in bounds[:-1]])

    def test_edge_connected_matting_preserves_enclosed_similar_color(self) -> None:
        rgb = np.zeros((50, 50, 3), dtype=np.uint8)
        rgb[:, :] = (0, 255, 0)
        rgb[10:40, 10:40] = (220, 30, 50)
        rgb[20:30, 20:30] = (0, 255, 0)
        rgba = np.asarray(remove_edge_background(rgb, hard_tolerance=20, soft_tolerance=40))
        self.assertEqual(int(rgba[0, 0, 3]), 0)
        self.assertEqual(int(rgba[25, 25, 3]), 255)

    def test_black_subject_on_black_plate_keeps_interior_opaque(self) -> None:
        rgb = np.zeros((80, 80, 3), dtype=np.uint8)
        rgb[18:62, 18:62] = (32, 24, 28)
        rgb[28:52, 28:52] = (48, 36, 40)
        rgba = np.asarray(remove_edge_background(rgb))
        interior = rgba[30:50, 30:50, 3]
        self.assertEqual(int(rgba[0, 0, 3]), 0)
        self.assertGreaterEqual(int(interior.min()), 250)
        self.assertGreaterEqual(float(np.mean(interior == 255)), 0.99)
        self.assertLess(subject_alpha_damage(rgba, np.array([0.0, 0.0, 0.0])), 0.05)

    def test_disconnected_neighbor_fragment_is_removed(self) -> None:
        rgba = np.zeros((40, 40, 4), dtype=np.uint8)
        rgba[10:30, 10:30] = (240, 60, 80, 255)
        rgba[15:20, 0:3] = (240, 60, 80, 255)
        rgba = np.asarray(Image.fromarray(rgba, mode="RGBA"))
        filtered, removed = remove_interior_border_fragments(rgba, 0, 1, 1, 2)
        self.assertEqual(removed, 1)
        self.assertEqual(int(filtered[17, 1, 3]), 0)
        self.assertEqual(int(filtered[20, 20, 3]), 255)

    def test_main_subject_crossing_internal_seam_is_rejected(self) -> None:
        rgba = np.zeros((40, 40, 4), dtype=np.uint8)
        rgba[10:30, 10:40] = (240, 60, 80, 255)
        with self.assertRaisesRegex(GridBoundaryError, "touches internal grid edge"):
            remove_interior_border_fragments(rgba, 0, 0, 1, 2)

    def test_full_frame_assignment_recovers_separable_crossing(self) -> None:
        rgba = np.zeros((80, 120, 4), dtype=np.uint8)
        rgba[20:62, 20:72] = (240, 60, 80, 255)
        rgba[22:60, 82:108] = (60, 90, 240, 255)
        ownership, valid, reasons, report = assign_grid_components(rgba, 2, 1)
        self.assertTrue(all(valid), reasons)
        self.assertGreater(int(np.count_nonzero(ownership[0][:, 60:])), 0)
        self.assertEqual(report["recovered_crossings"], 1)

    def test_full_frame_assignment_marks_fused_instances_ambiguous(self) -> None:
        rgba = np.zeros((80, 120, 4), dtype=np.uint8)
        rgba[18:62, 18:102] = (240, 60, 80, 255)
        ownership, valid, reasons, report = assign_grid_components(rgba, 2, 1)
        self.assertFalse(any(valid))
        self.assertEqual(report["ambiguous_components"], 1)
        self.assertIn("merged-instance-ambiguous", reasons[0])

    def test_subject_alpha_damage_flags_moth_eaten_dark_fur(self) -> None:
        rgba = np.zeros((80, 80, 4), dtype=np.uint8)
        rgba[18:62, 18:62] = (32, 24, 28, 48)
        rgba[28:52, 28:52] = (48, 36, 40, 90)
        self.assertGreater(subject_alpha_damage(rgba, np.array([0.0, 0.0, 0.0])), 0.12)

    def test_loop_selection_uses_native_frames_and_repairs_isolated_bad_frame(self) -> None:
        signatures = []
        for index in range(72):
            value = int(80 + 60 * np.sin(min(index, 47) * np.pi / 24)) if index < 48 else 80
            signatures.append(np.full((4, 4, 4), value, dtype=np.float32))
        valid = [True] * len(signatures)
        valid[12] = False
        result = select_loop_indices(valid, signatures, 24.0, 6, 1.5, 2.5)
        self.assertGreaterEqual(result["output_frames"], 9)
        self.assertLessEqual(result["output_frames"], 15)
        self.assertTrue(all(valid[index] for index in result["indices"]))
        self.assertLessEqual(result["duration_seconds"], 2.5)

    def test_full_duration_mode_keeps_every_native_frame(self) -> None:
        signatures = [np.full((4, 4, 4), index, dtype=np.float32) for index in range(145)]
        result = preserve_full_duration_indices([True] * 145, signatures, 24.0)
        self.assertEqual(result["mode"], "preserve-full-duration")
        self.assertEqual(result["indices"], list(range(145)))
        self.assertEqual(result["frame_step"], 1)
        self.assertEqual(result["output_frames"], 145)
        self.assertEqual(result["duration_seconds"], 6.041667)

    def test_full_duration_mode_refuses_to_skip_unsafe_frame(self) -> None:
        signatures = [np.zeros((4, 4, 4), dtype=np.float32) for _ in range(4)]
        with self.assertRaisesRegex(GridBoundaryError, "cannot skip unsafe native frames"):
            preserve_full_duration_indices([True, False, True, True], signatures, 24.0)

    def test_full_duration_sampling_keeps_four_seconds_at_twelve_fps(self) -> None:
        signatures = [np.full((4, 4, 4), index, dtype=np.float32) for index in range(96)]
        result = sample_full_duration_indices([True] * 96, signatures, 24.0, 12)
        self.assertEqual(result["mode"], "full-duration-sampled")
        self.assertEqual(result["output_frames"], 48)
        self.assertEqual(result["duration_seconds"], 4.0)
        self.assertEqual(result["frame_step"], 2.0)
        self.assertEqual(result["indices"], list(range(0, 96, 2)))

    def test_full_duration_sampling_repairs_only_inside_time_bin(self) -> None:
        signatures = [np.full((4, 4, 4), index, dtype=np.float32) for index in range(96)]
        valid = [True] * 96
        valid[20] = False
        result = sample_full_duration_indices(valid, signatures, 24.0, 12)
        self.assertEqual(result["indices"][10], 21)
        self.assertEqual(result["repairs"], 1)

    def test_short_delivery_uses_first_three_seconds(self) -> None:
        images = []
        for index in range(48):
            image = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            size = 24 + (20 if 6 <= index <= 17 else 0)
            inset = (80 - size) // 2
            draw.rectangle((inset, inset, inset + size, inset + size), fill=(240, 40, 80, 255))
            images.append(image)
        result = select_short_delivery_indices(images, 8, 3)
        self.assertEqual(result["mode"], "prefix-duration")
        self.assertEqual(result["indices"], list(range(24)))
        self.assertEqual(result["output_frames"], 24)
        self.assertEqual(result["endpoint_difference_policy"], "informational-only")

    def test_short_delivery_does_not_retime_to_later_matching_pose(self) -> None:
        images = []
        for index in range(48):
            image = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            size = 24 if index == 0 or index >= 27 else 64
            inset = (80 - size) // 2
            draw.rectangle((inset, inset, inset + size, inset + size), fill=(240, 40, 80, 255))
            images.append(image)
        result = select_short_delivery_indices(images, 8, 3)
        self.assertEqual(result["mode"], "prefix-duration")
        self.assertEqual(result["source_end_frame"], 24)
        self.assertEqual(result["output_frames"], 24)
        self.assertEqual(result["source_duration_seconds"], 3.0)

    def test_short_delivery_allows_different_start_and_end_poses(self) -> None:
        images = []
        for index in range(48):
            image = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            size = 24 if index == 0 else 64
            inset = (80 - size) // 2
            draw.rectangle((inset, inset, inset + size, inset + size), fill=(240, 40, 80, 255))
            images.append(image)
        result = select_short_delivery_indices(images, 8, 3)
        self.assertEqual(result["mode"], "prefix-duration")
        self.assertGreater(result["endpoint_difference"], 0.08)


    def test_blank_sheet_never_reports_high_confidence(self) -> None:
        image = Image.new("RGB", (400, 400), (255, 255, 255))
        report = detect_layout(image, [(2, 2), (3, 3), (4, 3)])
        self.assertLess(report["detected_layout"]["confidence"], 0.75)
        self.assertIn("one-or-more-detected-cells-appear-empty", report["warnings"])


if __name__ == "__main__":
    unittest.main()
