import os
import uuid
import logging
from werkzeug.utils import secure_filename

logger = logging.getLogger("internai.cv_service")

ALLOWED_EXTENSIONS = {"pdf", "docx"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def allowed_file(filename: str) -> bool:
    """Check if uploaded file has a supported extension."""
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def get_file_extension(filename: str) -> str:
    """Extract lowercase file extension."""
    if "." in filename:
        return filename.rsplit(".", 1)[1].lower()
    return ""


def save_and_extract_cv(file_storage, upload_folder: str) -> tuple[str, str, str]:
    """
    Save uploaded file securely and extract its text content.
    
    Returns:
        tuple[filename: str, saved_path: str, extracted_text: str]
        
    Raises:
        ValueError: If file is missing, invalid extension, exceeds size, or extraction fails.
    """
    if not file_storage or not file_storage.filename:
        raise ValueError("No file uploaded or file is missing a name.")

    filename = file_storage.filename
    if not allowed_file(filename):
        raise ValueError(f"Invalid file type. Allowed formats: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    # Sanitize and assign unique filename to avoid collision
    base_name = secure_filename(filename)
    unique_prefix = uuid.uuid4().hex[:8]
    saved_filename = f"{unique_prefix}_{base_name}"
    
    os.makedirs(upload_folder, exist_ok=True)
    saved_path = os.path.join(upload_folder, saved_filename)

    file_storage.save(saved_path)

    # Check file size on disk
    file_size = os.path.getsize(saved_path)
    if file_size > MAX_FILE_SIZE:
        os.remove(saved_path)
        raise ValueError(f"File size ({file_size / (1024*1024):.2f} MB) exceeds maximum allowed limit (5 MB).")

    if file_size == 0:
        os.remove(saved_path)
        raise ValueError("Uploaded file is empty (0 bytes).")

    ext = get_file_extension(saved_filename)
    try:
        if ext == "pdf":
            text = extract_text_from_pdf(saved_path)
        elif ext == "docx":
            text = extract_text_from_docx(saved_path)
        else:
            raise ValueError(f"Unsupported format: {ext}")
    except Exception as e:
        logger.error(f"Error parsing {saved_filename}: {e}", exc_info=True)
        raise ValueError(f"Failed to extract text from {filename}: {str(e)}")

    clean_text = clean_extracted_text(text)
    if not clean_text or len(clean_text) < 30:
        raise ValueError("Extracted text is too short or empty. Please ensure the document is not an image-only scan.")

    return saved_filename, saved_path, clean_text


def extract_text_from_pdf(file_path: str) -> str:
    """Extract textual content from a PDF document using pypdf."""
    text_chunks = []

    # 1. Try with pypdf
    try:
        import pypdf
        reader = pypdf.PdfReader(file_path)
        for page_idx, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text_chunks.append(page_text)
    except Exception as e:
        logger.warning(f"pypdf extraction warning on {file_path}: {e}")

    # 2. Try pdfplumber if pypdf returned empty and pdfplumber is installed
    if not text_chunks:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text()
                    if txt:
                        text_chunks.append(txt)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"pdfplumber extraction warning on {file_path}: {e}")

    return "\n\n".join(text_chunks)


def extract_text_from_docx(file_path: str) -> str:
    """Extract textual content from a Microsoft Word .docx file."""
    import docx
    doc = docx.Document(file_path)
    paragraphs = []
    
    # Extract standard paragraphs
    for p in doc.paragraphs:
        if p.text.strip():
            paragraphs.append(p.text)
            
    # Extract text from tables if present
    for table in doc.tables:
        for row in table.rows:
            row_text = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if row_text:
                paragraphs.append(" | ".join(row_text))

    return "\n\n".join(paragraphs)


def clean_extracted_text(text: str) -> str:
    """Normalize extracted document text."""
    if not text:
        return ""
    # Strip null bytes and non-printable control characters
    text = text.replace("\x00", "")
    lines = [line.strip() for line in text.splitlines()]
    # Remove duplicate blank lines
    cleaned_lines = []
    prev_blank = False
    for line in lines:
        if not line:
            if not prev_blank:
                cleaned_lines.append("")
                prev_blank = True
        else:
            cleaned_lines.append(line)
            prev_blank = False

    return "\n".join(cleaned_lines).strip()
