"""
tests/test_image_classification_ocr.py

Unit tests for image classification, OCR decision rules, graceful OCR degradation,
and end-to-end wiring of ImageOCRMetadata into ImageReference.

Covers:
  - All 10 classification rules in StackOverflowParser._classify_image()
  - All branches in StackOverflowParser._should_ocr()
  - _run_ocr() graceful degradation when OCR library is not installed
  - _extract_image_references() attaches ocr_metadata to each ImageReference
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from rag_engine.ingestion.parsers.stackoverflow_parser import (
    IMAGE_CLASS_CONFIG_SCREEN,
    IMAGE_CLASS_ERROR_DIALOG,
    IMAGE_CLASS_FLOWCHART,
    IMAGE_CLASS_OTHER,
    IMAGE_CLASS_SCREENSHOT,
    IMAGE_CLASS_STACKTRACE,
    IMAGE_CLASS_TABLE,
    IMAGE_CLASS_UI_SCREEN,
    IMAGE_CLASS_WHATSAPP_CHAT,
    ImageOCRMetadata,
    ImageReference,
    StackOverflowParser,
    _extract_image_references,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PARSER = StackOverflowParser()

_SAMPLE_IMAGE_URL = (
    "https://stackoverflowteams.com/c/kwikid/images/s/"
    "ec268e79-c0ef-463c-96ea-1fca0fefb5f4.png"
)
_SAMPLE_GUID = "ec268e79-c0ef-463c-96ea-1fca0fefb5f4"


# ---------------------------------------------------------------------------
# Classification — Rule 1: WHATSAPP_CHAT
# ---------------------------------------------------------------------------

class TestClassifyWhatsappChat:
    def test_alt_contains_whatsapp(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "whatsapp screenshot", "Some body text")
        assert result == IMAGE_CLASS_WHATSAPP_CHAT

    def test_alt_contains_chat(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "chat log", "Some body text")
        assert result == IMAGE_CLASS_WHATSAPP_CHAT

    def test_body_contains_whatsapp(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "User sent a message via whatsapp")
        assert result == IMAGE_CLASS_WHATSAPP_CHAT

    def test_no_whatsapp_keywords_does_not_match(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "diagram", "Some process steps")
        assert result != IMAGE_CLASS_WHATSAPP_CHAT


# ---------------------------------------------------------------------------
# Classification — Rule 2: FLOWCHART
# ---------------------------------------------------------------------------

class TestClassifyFlowchart:
    def test_alt_contains_flow(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "flow diagram", "Some body")
        assert result == IMAGE_CLASS_FLOWCHART

    def test_body_contains_flow_and_diagram(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "See the flow diagram below for the process")
        assert result == IMAGE_CLASS_FLOWCHART

    def test_body_contains_flow_and_chart(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "This flow chart describes the process")
        assert result == IMAGE_CLASS_FLOWCHART

    def test_body_contains_flow_and_steps(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "The flow of steps is shown here")
        assert result == IMAGE_CLASS_FLOWCHART

    def test_flow_alone_in_body_without_supporting_word_does_not_match(self):
        # "flow" without "diagram", "chart", or "steps" should not trigger Rule 2
        # unless alt text contains "flow"
        result = PARSER._classify_image(_SAMPLE_GUID, "", "This is the main flow of the API")
        # Should NOT be FLOWCHART (falls through to subsequent rules)
        assert result != IMAGE_CLASS_FLOWCHART


# ---------------------------------------------------------------------------
# Classification — Rule 3: STACKTRACE
# ---------------------------------------------------------------------------

class TestClassifyStacktrace:
    def test_body_contains_stack_trace(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "Here is the stack trace from the error")
        assert result == IMAGE_CLASS_STACKTRACE

    def test_body_contains_traceback(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "Python traceback shown in the image")
        assert result == IMAGE_CLASS_STACKTRACE

    def test_body_contains_exception(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "An exception occurred as shown below")
        assert result == IMAGE_CLASS_STACKTRACE


# ---------------------------------------------------------------------------
# Classification — Rule 4: ERROR_DIALOG
# ---------------------------------------------------------------------------

class TestClassifyErrorDialog:
    def test_body_contains_error_and_short_body(self):
        short_body = "error"  # len < 100, contains "error"
        result = PARSER._classify_image(_SAMPLE_GUID, "", short_body)
        assert result == IMAGE_CLASS_ERROR_DIALOG

    def test_body_contains_error_but_long_body_does_not_match_rule4(self):
        # Long body with "error" falls through to later rules, not ERROR_DIALOG
        long_body = "error " + "x" * 200
        result = PARSER._classify_image(_SAMPLE_GUID, "", long_body)
        assert result != IMAGE_CLASS_ERROR_DIALOG


# ---------------------------------------------------------------------------
# Classification — Rule 5: CONFIG_SCREEN
# ---------------------------------------------------------------------------

class TestClassifyConfigScreen:
    def test_body_contains_config(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "Go to config and update the value")
        assert result == IMAGE_CLASS_CONFIG_SCREEN

    def test_body_contains_configuration(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "See the configuration panel below")
        assert result == IMAGE_CLASS_CONFIG_SCREEN

    def test_body_contains_setting(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "Navigate to the setting page")
        assert result == IMAGE_CLASS_CONFIG_SCREEN


# ---------------------------------------------------------------------------
# Classification — Rule 6: TABLE
# ---------------------------------------------------------------------------

class TestClassifyTable:
    def test_body_contains_table(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "The table below shows all records")
        assert result == IMAGE_CLASS_TABLE

    def test_body_contains_columns(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "Review the columns in this export")
        assert result == IMAGE_CLASS_TABLE

    def test_body_contains_schema(self):
        result = PARSER._classify_image(_SAMPLE_GUID, "", "This is the database schema")
        assert result == IMAGE_CLASS_TABLE


# ---------------------------------------------------------------------------
# Classification — Rule 7 / 9: SCREENSHOT (short vs long body)
# ---------------------------------------------------------------------------

class TestClassifyScreenshot:
    def test_classify_screenshot_short_body(self):
        """Rule 7: bodyMarkdown after stripping image syntax is < 30 chars → SCREENSHOT."""
        # After stripping the image markdown, remaining text is very short
        img_md = "![img](https://stackoverflowteams.com/c/kwikid/images/s/ec268e79-c0ef-463c-96ea-1fca0fefb5f4.png)"
        result = PARSER._classify_image(_SAMPLE_GUID, "img", img_md)
        assert result == IMAGE_CLASS_SCREENSHOT

    def test_classify_screenshot_long_body(self):
        """Rule 9: bodyMarkdown > 200 chars, no other keyword matches → SCREENSHOT."""
        long_body = "This is a detailed explanation of the process. " * 10
        result = PARSER._classify_image(_SAMPLE_GUID, "screenshot", long_body)
        assert result == IMAGE_CLASS_SCREENSHOT


# ---------------------------------------------------------------------------
# Classification — Rule 8: UI_SCREEN
# ---------------------------------------------------------------------------

class TestClassifyUIScreen:
    def test_body_contains_portal(self):
        # 31–200 chars, contains "portal" → UI_SCREEN
        body = "Go to the portal and click the button here for the feature"
        # Ensure it's in the 30–200 char range and no prior rule fires
        assert 30 <= len(body) <= 200
        result = PARSER._classify_image(_SAMPLE_GUID, "", body)
        assert result == IMAGE_CLASS_UI_SCREEN

    def test_body_contains_dashboard(self):
        body = "Open the dashboard view to see the widget list"
        assert 30 <= len(body) <= 200
        result = PARSER._classify_image(_SAMPLE_GUID, "", body)
        assert result == IMAGE_CLASS_UI_SCREEN


# ---------------------------------------------------------------------------
# Classification — Rule 10: OTHER (default)
# ---------------------------------------------------------------------------

class TestClassifyDefault:
    def test_no_rule_matches_returns_other(self):
        # 31–200 chars, no matching keyword
        body = "Please see the attached image for reference and review"
        assert 30 <= len(body) <= 200
        result = PARSER._classify_image(_SAMPLE_GUID, "", body)
        assert result == IMAGE_CLASS_OTHER


# ---------------------------------------------------------------------------
# OCR Decision Rules
# ---------------------------------------------------------------------------

class TestOCRDecision:
    def test_ocr_decision_whatsapp_chat(self):
        """WHATSAPP_CHAT → OCR_REQUIRED True regardless of body length."""
        assert PARSER._should_ocr(IMAGE_CLASS_WHATSAPP_CHAT, 500) is True
        assert PARSER._should_ocr(IMAGE_CLASS_WHATSAPP_CHAT, 10) is True

    def test_ocr_decision_stacktrace(self):
        assert PARSER._should_ocr(IMAGE_CLASS_STACKTRACE, 500) is True

    def test_ocr_decision_error_dialog(self):
        assert PARSER._should_ocr(IMAGE_CLASS_ERROR_DIALOG, 50) is True

    def test_ocr_decision_config_screen(self):
        assert PARSER._should_ocr(IMAGE_CLASS_CONFIG_SCREEN, 200) is True

    def test_ocr_decision_table(self):
        assert PARSER._should_ocr(IMAGE_CLASS_TABLE, 300) is True

    def test_ocr_decision_flowchart(self):
        assert PARSER._should_ocr(IMAGE_CLASS_FLOWCHART, 100) is True

    def test_ocr_decision_ui_screen(self):
        """UI_SCREEN → OCR_REQUIRED False (non-specific labels)."""
        assert PARSER._should_ocr(IMAGE_CLASS_UI_SCREEN, 50) is False
        assert PARSER._should_ocr(IMAGE_CLASS_UI_SCREEN, 500) is False

    def test_ocr_decision_other(self):
        """OTHER → OCR_REQUIRED False (conservative default)."""
        assert PARSER._should_ocr(IMAGE_CLASS_OTHER, 50) is False

    def test_ocr_decision_screenshot_short_body(self):
        """SCREENSHOT + body_len < 100 → OCR required (image is primary content)."""
        assert PARSER._should_ocr(IMAGE_CLASS_SCREENSHOT, 80) is True

    def test_ocr_decision_screenshot_long_body(self):
        """SCREENSHOT + body_len >= 100 → OCR not required (surrounding text sufficient)."""
        assert PARSER._should_ocr(IMAGE_CLASS_SCREENSHOT, 100) is False
        assert PARSER._should_ocr(IMAGE_CLASS_SCREENSHOT, 500) is False


# ---------------------------------------------------------------------------
# _run_ocr graceful degradation
# ---------------------------------------------------------------------------

class TestRunOCRGracefulDegradation:
    def test_ocr_returns_none_text_when_library_unavailable(self):
        """
        When rapidocr-onnxruntime is not installed, _run_ocr must return
        ImageOCRMetadata with ocr_text=None and must not raise.
        """
        # Temporarily make "rapidocr_onnxruntime" unimportable
        original = sys.modules.get("rapidocr_onnxruntime")
        sys.modules["rapidocr_onnxruntime"] = None  # type: ignore[assignment]
        try:
            result = PARSER._run_ocr("/tmp/fake.png", _SAMPLE_GUID, IMAGE_CLASS_SCREENSHOT)
        finally:
            if original is None:
                sys.modules.pop("rapidocr_onnxruntime", None)
            else:
                sys.modules["rapidocr_onnxruntime"] = original

        assert isinstance(result, ImageOCRMetadata)
        assert result.ocr_text is None
        assert result.ocr_required is True
        assert result.image_guid == _SAMPLE_GUID

    def test_ocr_returns_none_text_when_ocr_raises_unexpected_error(self):
        """
        If the OCR engine raises any unexpected exception, _run_ocr must catch it
        and return metadata with ocr_text=None rather than propagating.
        """
        mock_module = types.ModuleType("rapidocr_onnxruntime")
        mock_ocr_cls = MagicMock(side_effect=RuntimeError("ONNX model load failed"))
        mock_module.RapidOCR = mock_ocr_cls  # type: ignore[attr-defined]

        original = sys.modules.get("rapidocr_onnxruntime")
        sys.modules["rapidocr_onnxruntime"] = mock_module
        try:
            result = PARSER._run_ocr("/tmp/fake.png", _SAMPLE_GUID, IMAGE_CLASS_STACKTRACE)
        finally:
            if original is None:
                sys.modules.pop("rapidocr_onnxruntime", None)
            else:
                sys.modules["rapidocr_onnxruntime"] = original

        assert isinstance(result, ImageOCRMetadata)
        assert result.ocr_text is None
        assert result.ocr_required is True

    def test_ocr_returns_valid_metadata_on_success(self):
        """
        When RapidOCR returns valid results, _run_ocr should return metadata
        with ocr_text populated and ocr_version='rapidocr-1.0'.
        """
        fake_result = [
            [[[0, 0], [100, 0], [100, 20], [0, 20]], "Hello World", 0.98],
            [[[0, 25], [100, 25], [100, 45], [0, 45]], "Error 404", 0.92],
        ]
        mock_engine = MagicMock(return_value=(fake_result, 0.1))
        mock_cls    = MagicMock(return_value=mock_engine)
        mock_module = types.ModuleType("rapidocr_onnxruntime")
        mock_module.RapidOCR = mock_cls  # type: ignore[attr-defined]

        original = sys.modules.get("rapidocr_onnxruntime")
        sys.modules["rapidocr_onnxruntime"] = mock_module
        try:
            result = PARSER._run_ocr("/tmp/fake.png", _SAMPLE_GUID, IMAGE_CLASS_ERROR_DIALOG)
        finally:
            if original is None:
                sys.modules.pop("rapidocr_onnxruntime", None)
            else:
                sys.modules["rapidocr_onnxruntime"] = original

        assert isinstance(result, ImageOCRMetadata)
        assert result.ocr_text == "Hello World\nError 404"
        assert result.ocr_confidence is not None
        assert abs(result.ocr_confidence - round((0.98 + 0.92) / 2, 4)) < 0.001
        assert result.ocr_version == "rapidocr-1.0"


# ---------------------------------------------------------------------------
# End-to-end: _extract_image_references attaches ocr_metadata
# ---------------------------------------------------------------------------

class TestImageReferenceHasOCRMetadata:
    def _make_minimal_data_dir(self, tmp_path: Path) -> Path:
        """Create a minimal images directory with no actual PNG files (local_path=None)."""
        (tmp_path / "images").mkdir()
        return tmp_path

    def test_image_reference_has_ocr_metadata_set(self, tmp_path):
        """
        _extract_image_references() must attach an ImageOCRMetadata instance
        to each ImageReference when `parser` is provided.
        """
        question_markdown = (
            "Please see the whatsapp screenshot below.\n"
            f"![whatsapp chat]({_SAMPLE_IMAGE_URL})"
        )
        data_dir = self._make_minimal_data_dir(tmp_path)
        image_map = {_SAMPLE_GUID: {"id": 808, "imageGuid": _SAMPLE_GUID}}

        refs = _extract_image_references(
            question_markdown=question_markdown,
            answer_markdown="",
            image_map=image_map,
            data_dir=data_dir,
            title_and_tags="",
            parser=PARSER,
        )

        assert len(refs) == 1
        ref = refs[0]
        assert ref.guid == _SAMPLE_GUID
        assert ref.ocr_metadata is not None
        assert isinstance(ref.ocr_metadata, ImageOCRMetadata)
        assert ref.ocr_metadata.image_class == IMAGE_CLASS_WHATSAPP_CHAT
        assert ref.ocr_metadata.ocr_required is True
        # local_path is None because we have no actual PNG file in tmp_path
        assert ref.ocr_metadata.image_path == ""
        assert ref.ocr_metadata.ocr_text is None  # not run yet (no local file)

    def test_image_reference_without_parser_has_no_ocr_metadata(self, tmp_path):
        """
        When `parser` is NOT provided, ocr_metadata stays None (backward compat).
        """
        question_markdown = (
            f"![img]({_SAMPLE_IMAGE_URL})"
        )
        data_dir = self._make_minimal_data_dir(tmp_path)

        refs = _extract_image_references(
            question_markdown=question_markdown,
            answer_markdown="",
            image_map={},
            data_dir=data_dir,
            # parser not provided → old behaviour
        )

        assert len(refs) == 1
        assert refs[0].ocr_metadata is None

    def test_ocr_metadata_to_dict_is_serialisable(self, tmp_path):
        """
        ImageOCRMetadata.to_dict() must produce a plain JSON-serialisable dict.
        """
        metadata = ImageOCRMetadata(
            image_guid=_SAMPLE_GUID,
            image_path="/path/to/image.png",
            image_class=IMAGE_CLASS_CONFIG_SCREEN,
            ocr_required=True,
            ocr_text="Some extracted text",
            ocr_confidence=0.95,
            ocr_version="rapidocr-1.0",
        )
        d = metadata.to_dict()
        # Must be JSON-serialisable without error
        serialised = json.dumps(d)
        loaded = json.loads(serialised)

        assert loaded["image_guid"] == _SAMPLE_GUID
        assert loaded["image_class"] == IMAGE_CLASS_CONFIG_SCREEN
        assert loaded["ocr_required"] is True
        assert loaded["ocr_text"] == "Some extracted text"
        assert abs(loaded["ocr_confidence"] - 0.95) < 0.001
        assert loaded["ocr_version"] == "rapidocr-1.0"

    def test_ui_screen_image_does_not_trigger_ocr(self, tmp_path):
        """
        Images classified as UI_SCREEN should have ocr_required=False.
        """
        body = (
            "Open the portal and navigate to the settings page.\n"
            f"![portal view]({_SAMPLE_IMAGE_URL})"
        )
        data_dir = self._make_minimal_data_dir(tmp_path)
        image_map = {_SAMPLE_GUID: {"id": 808, "imageGuid": _SAMPLE_GUID}}

        refs = _extract_image_references(
            question_markdown=body,
            answer_markdown="",
            image_map=image_map,
            data_dir=data_dir,
            title_and_tags="portal ui",
            parser=PARSER,
        )

        assert len(refs) == 1
        meta = refs[0].ocr_metadata
        assert meta is not None
        assert meta.ocr_required is False
