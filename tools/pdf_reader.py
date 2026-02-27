"""
PDF reader tool — extract text and tables from tender documents.
Used by Agent-S (mining) and Agent-RM (compliance checking).
"""

import structlog
import pdfplumber

logger = structlog.get_logger()


def extract_text_from_pdf(file_path: str) -> str:
    """Extract all text from a PDF file."""
    try:
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        full_text = "\n\n".join(text_parts)
        logger.info("pdf_extracted", file=file_path, chars=len(full_text))
        return full_text
    except Exception as e:
        logger.error("pdf_extract_failed", file=file_path, error=str(e))
        return ""


def extract_tables_from_pdf(file_path: str) -> list[list[list[str]]]:
    """Extract all tables from a PDF. Returns list of tables, each a list of rows."""
    try:
        all_tables = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                if tables:
                    all_tables.extend(tables)
        logger.info("pdf_tables_extracted", file=file_path, tables=len(all_tables))
        return all_tables
    except Exception as e:
        logger.error("pdf_tables_failed", file=file_path, error=str(e))
        return []
