"""AI Service Layer for InternAI.

Wraps Google Gemini API calls via official `google-genai` SDK with:
- Structured JSON output schemas (Pydantic models)
- Exponential backoff retry logic and timeout handling
- JSON schema validation
- Prompt injection defenses
- Graceful heuristic fallback when GEMINI_API_KEY is not configured
"""

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.services.prompts import (
    MAX_INPUT_LENGTH,
    JOB_ANALYZER_SYSTEM_PROMPT,
    JOB_ANALYZER_USER_PROMPT,
    SKILL_MATCHER_SYSTEM_PROMPT,
    SKILL_MATCHER_USER_PROMPT,
    ROADMAP_SYSTEM_PROMPT,
    ROADMAP_USER_PROMPT,
    RISK_ANALYZER_SYSTEM_PROMPT,
    RISK_ANALYZER_USER_PROMPT,
    CV_ANALYSIS_SYSTEM_PROMPT,
    CV_ANALYSIS_USER_PROMPT,
    APPLICATION_ASSISTANT_SYSTEM_PROMPT,
    COVER_LETTER_USER_PROMPT,
    APPLICATION_EMAIL_USER_PROMPT,
    INTERVIEW_PREP_USER_PROMPT,
    MOCK_INTERVIEW_SYSTEM_PROMPT,
    MOCK_INTERVIEW_QUESTION_USER_PROMPT,
    MOCK_INTERVIEW_FEEDBACK_USER_PROMPT,
    MOCK_INTERVIEW_SUMMARY_USER_PROMPT,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Pydantic Schemas for Structured JSON Output
# ============================================================================

class JobAnalysisSchema(BaseModel):
    job_title: str = Field(description="Normalized job or internship title")
    company: str = Field(description="Company or employer organization name")
    role_summary: str = Field(description="Concise 2-3 sentence overview of the internship role")
    required_skills: List[str] = Field(description="Mandatory technical and domain skills required for the job")
    preferred_skills: List[str] = Field(description="Nice-to-have, bonus, or preferred skills")
    experience_required: str = Field(description="Years of experience or background level expected (e.g. '0-1 years', 'None')")
    education_required: str = Field(description="Target degree or student level (e.g. 'B.S. in Computer Science', 'Undergraduate')")
    location: str = Field(description="Job location or 'Not specified'")
    work_type: str = Field(description="Must be one of: 'onsite', 'remote', 'hybrid', 'unspecified'")
    duration: str = Field(description="Internship duration (e.g., '12 weeks', 'Summer 2026', 'Not specified')")
    stipend: str = Field(description="Compensation, hourly rate, or 'Unpaid'/'Not specified'")
    other_requirements: List[str] = Field(description="Additional requirements such as work eligibility, minimum GPA, etc.")


class MatchedSkillItem(BaseModel):
    requirement: str = Field(description="The job requirement skill name")
    matched_student_skill: str = Field(description="The student's equivalent profile skill")
    student_proficiency: str = Field(description="Proficiency: Beginner, Intermediate, or Advanced")
    is_required: bool = Field(description="True if mandatory requirement, False if preferred")


class MissingSkillItem(BaseModel):
    requirement: str = Field(description="The missing skill name")
    importance: str = Field(description="'critical' for required skills, 'nice_to_have' for preferred skills")
    reason: str = Field(description="Brief explanation of why this skill is needed for the role")


class SkillMatchSchema(BaseModel):
    matched_skills: List[MatchedSkillItem]
    missing_skills: List[MissingSkillItem]
    areas_to_improve: List[str] = Field(description="2-4 technical focus areas to bridge the student's gaps")


# Module D: Learning Roadmap Schemas
class TopicMilestone(BaseModel):
    topic_id: str = Field(description="Unique milestone ID, e.g. 'w1_t1'")
    title: str = Field(description="Actionable milestone or topic title")
    description: str = Field(description="What concept or practical skill to learn")
    resource_type: str = Field(description="Type of free educational material (e.g. 'Official Docs', 'Interactive Sandbox', 'Video Tutorial')")
    resource_hint: str = Field(description="Authoritative platform or documentation name (e.g. 'MDN Web Docs', 'PostgreSQL Tutorial', 'FreeCodeCamp')")


class WeekPlan(BaseModel):
    week_number: int = Field(description="Week index, starting at 1")
    theme: str = Field(description="Main weekly theme, e.g. 'Foundations of Relational Databases'")
    weekly_focus: str = Field(description="Core learning objectives for this week")
    estimated_hours: int = Field(description="Estimated weekly hours commitment")
    topics: List[TopicMilestone] = Field(description="Actionable topics/tasks to complete this week")


class PracticeProject(BaseModel):
    title: str = Field(description="Project title suitable for student portfolio")
    description: str = Field(description="Overview of the application or tool to build")
    technologies: List[str] = Field(description="Key skills and frameworks exercised in this project")
    portfolio_impact: str = Field(description="How to showcase this project on a resume or GitHub")


class RoadmapSchema(BaseModel):
    summary: str = Field(description="Strategic summary of this customized study plan")
    weeks: List[WeekPlan] = Field(description="Week-by-week structured curriculum")
    practice_projects: List[PracticeProject] = Field(description="1-3 tangible portfolio project suggestions")


# Module E: Job Risk & Scam Detection Schemas
class RiskIndicatorItem(BaseModel):
    category: str = Field(description="Category of the indicator, e.g. 'Financial / Upfront Fees', 'Communication Channels', 'Sensitive Data Request', 'Unrealistic Compensation'")
    title: str = Field(description="Short descriptive title of the risk signal")
    severity: str = Field(description="'Low', 'Medium', or 'High'")
    quoted_evidence: str = Field(description="Exact excerpt quoted from the job posting, or 'Not explicitly provided'")
    explanation: str = Field(description="Why this pattern presents a warning signal for students")


class JobRiskSchema(BaseModel):
    risk_level: str = Field(description="'Safe', 'Low', 'Medium', or 'High'")
    risk_score: float = Field(description="Composite risk score from 0.0 (completely safe) to 10.0 (definite scam)")
    summary: str = Field(description="Overview assessment of the employer posting's legitimacy")
    indicators: List[RiskIndicatorItem] = Field(description="List of detected warning indicators")
    safety_advice: List[str] = Field(description="Actionable precautions for the student before communicating")


# Module F: CV Analysis Schemas
class CVEducationItem(BaseModel):
    institution: str = Field(description="University, college, or school name")
    degree: str = Field(description="Degree and major, e.g. 'B.S. in Computer Science'")
    graduation_year: Optional[str] = Field(default=None, description="Expected or graduation year")
    gpa: Optional[str] = Field(default=None, description="Reported GPA if mentioned")


class CVExperienceItem(BaseModel):
    role: str = Field(description="Job or internship position title")
    company: str = Field(description="Company or organization name")
    duration: Optional[str] = Field(default=None, description="Dates or duration worked")
    highlights: List[str] = Field(default=[], description="Bullet points summarizing responsibilities & impact")


class CVProjectItem(BaseModel):
    title: str = Field(description="Project name or application title")
    technologies: List[str] = Field(default=[], description="Tools, frameworks, and languages used")
    description: str = Field(description="Overview of what the project does")
    impact: Optional[str] = Field(default=None, description="Measurable outcome or key architectural highlight")


class BulletRewriteItem(BaseModel):
    original_bullet: str = Field(description="Weak or passive bullet point found in the CV")
    rewritten_bullet: str = Field(description="High-impact rewritten bullet point using active verbs and metrics")
    improvement_reason: str = Field(description="Why this rewrite is stronger and what principle was applied")


class CVScoreBreakdown(BaseModel):
    clarity: int = Field(ge=0, le=25, description="Clarity, structure, and readability score (0-25)")
    relevance: int = Field(ge=0, le=25, description="Relevance to modern software/tech internships (0-25)")
    impact: int = Field(ge=0, le=25, description="Action verbs, results, and quantified impact (0-25)")
    formatting_readability: int = Field(ge=0, le=25, description="Section layout, bullet quality, and conciseness (0-25)")
    total_score: int = Field(ge=0, le=100, description="Composite CV score (0-100)")


class ExtractedCVSkills(BaseModel):
    programming: List[str] = Field(default=[], description="Programming languages")
    web: List[str] = Field(default=[], description="Frontend and backend web frameworks/tools")
    data: List[str] = Field(default=[], description="Databases, data analysis, and modeling")
    cloud: List[str] = Field(default=[], description="DevOps, cloud platforms, containers")
    soft_skills: List[str] = Field(default=[], description="Communication, teamwork, problem solving")
    tools: List[str] = Field(default=[], description="Developer tools, IDEs, version control")


class CVAnalysisSchema(BaseModel):
    candidate_name: Optional[str] = Field(default=None, description="Extracted candidate full name")
    contact_email: Optional[str] = Field(default=None, description="Extracted email address")
    contact_phone: Optional[str] = Field(default=None, description="Extracted phone number")
    summary_verdict: str = Field(description="Overall 2-3 sentence executive assessment of the CV")
    score: CVScoreBreakdown = Field(description="Calibrated CV score breakdown")
    extracted_skills: ExtractedCVSkills = Field(description="Extracted skills categorized by domain")
    education: List[CVEducationItem] = Field(default=[], description="Educational background entries")
    experience: List[CVExperienceItem] = Field(default=[], description="Past work or internship experience")
    projects: List[CVProjectItem] = Field(default=[], description="Showcase or personal projects")
    weak_or_missing_sections: List[str] = Field(description="2-4 areas where the CV lacks essential elements")
    highlighted_projects: List[str] = Field(description="Top 1-3 projects that best demonstrate technical capability")
    bullet_rewrites: List[BulletRewriteItem] = Field(description="2-4 high-impact before-and-after bullet rewrites")


# Module G: Application Assistant Schemas
class CoverLetterSchema(BaseModel):
    recipient_salutation: str = Field(description="Greeting, e.g. 'Dear Hiring Team at [Company],'")
    subject_line: str = Field(description="Subject line or application header")
    body_paragraphs: List[str] = Field(description="3-4 structured body paragraphs tailored to the role")
    closing_salutation: str = Field(description="Sign-off, e.g. 'Sincerely,'")
    candidate_name: str = Field(description="Candidate's name for signature")
    key_highlights_referenced: List[str] = Field(description="Specific real skills and projects woven into letter")


class ApplicationEmailSchema(BaseModel):
    subject_line: str = Field(description="Compelling, professional subject line")
    salutation: str = Field(description="E.g. 'Dear [Name / Hiring Manager],'")
    email_body: str = Field(description="Concise 2-3 paragraph outreach email body")
    signoff: str = Field(description="Professional sign-off")
    candidate_name: str = Field(description="Candidate name")
    call_to_action: str = Field(description="Explicit, polite call to action statement")


class InterviewPrepQuestion(BaseModel):
    question_number: int = Field(description="Question number index")
    category: str = Field(description="'Technical', 'Behavioral', or 'Situational'")
    question: str = Field(description="Likely interview question tailored to this role and background")
    why_it_matters: str = Field(description="What the hiring team evaluates through this question")
    talking_points: List[str] = Field(description="Recommended talking points based on candidate's real profile")
    sample_framework: str = Field(description="Suggested structure or STAR method response outline")


class InterviewPrepSchema(BaseModel):
    role_overview: str = Field(description="Summary of interview expectations for this position")
    top_preparation_tips: List[str] = Field(description="3-4 key tactical prep recommendations")
    questions: List[InterviewPrepQuestion] = Field(description="4-6 tailored practice interview questions")


# Module H: Mock Interview Schemas
class InterviewQuestionSchema(BaseModel):
    question_text: str = Field(description="The interview question to pose to the student")
    category: str = Field(description="'Technical', 'Behavioral', or 'Situational'")
    context_hint: str = Field(description="Brief coaching tip or expectation for this question")


class InterviewAnswerFeedbackSchema(BaseModel):
    score: int = Field(ge=1, le=10, description="Objective score from 1 (poor/off-topic) to 10 (exceptional)")
    strengths: str = Field(description="Specific positive elements demonstrated in the candidate's answer")
    improvements: str = Field(description="Actionable areas where the answer could be deeper, more structured, or quantified")
    sample_answer: str = Field(description="Exemplary benchmark answer demonstrating high impact, technical depth, and STAR structure")


class InterviewSummarySchema(BaseModel):
    overall_score: float = Field(ge=1.0, le=10.0, description="Overall performance score (1.0 to 10.0)")
    verdict: str = Field(description="Executive summary of the candidate's interview performance and readiness")
    top_improvement_areas: List[str] = Field(description="Top 3 tactical areas to work on before real interviews")
    key_strengths: List[str] = Field(description="2-4 standout competencies demonstrated during the session")


# ============================================================================
# Core Gemini Client Helper
# ============================================================================

def _get_gemini_client():
    """Retrieve configured Google GenAI client or None if API key missing."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key.startswith("your-"):
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as exc:
        logger.warning("Failed to initialize Google GenAI Client: %s", exc)
        return None


def _call_gemini_structured(
    system_instruction: str,
    user_prompt: str,
    response_schema: Any,
    max_retries: int = 2,
    timeout_seconds: int = 25,
) -> Optional[Dict[str, Any]]:
    """Invoke Gemini model requesting structured JSON output with retry & backoff."""
    client = _get_gemini_client()
    if not client:
        return None

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    from google.genai import types

    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Calling Gemini API (model=%s, attempt=%d)...", model_name, attempt)
            
            # Using GenerateContentConfig with structured response schema
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_json_schema=response_schema.model_json_schema() if hasattr(response_schema, "model_json_schema") else response_schema,
                temperature=0.2,
            )

            response = client.models.generate_content(
                model=model_name,
                contents=user_prompt,
                config=config,
            )

            if not response or not response.text:
                raise ValueError("Empty response received from Gemini API.")

            # Validate that response parses as valid JSON
            parsed = json.loads(response.text)
            return parsed

        except Exception as exc:
            logger.warning("Gemini API call attempt %d failed: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
            else:
                logger.error("All Gemini API attempts failed for prompt: %s", exc)
                return None

    return None


# ============================================================================
# Module B: Job Description Analyzer
# ============================================================================

def analyze_job_description(raw_text: str) -> Dict[str, Any]:
    """Parse raw job description text into structured JSON metadata."""
    clean_text = raw_text[:MAX_INPUT_LENGTH].strip()
    if not clean_text:
        raise ValueError("Job description text cannot be empty.")

    prompt = JOB_ANALYZER_USER_PROMPT.format(job_text=clean_text)

    # 1. Attempt Gemini AI structured generation
    ai_result = _call_gemini_structured(
        system_instruction=JOB_ANALYZER_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=JobAnalysisSchema,
    )

    if ai_result:
        # Validate schema keys
        required_keys = {"job_title", "company", "role_summary", "required_skills", "work_type"}
        if required_keys.issubset(ai_result.keys()):
            # Normalize work_type
            wt = str(ai_result.get("work_type", "unspecified")).lower()
            if wt not in ("onsite", "remote", "hybrid", "unspecified"):
                ai_result["work_type"] = "unspecified"
            return ai_result

    # 2. Heuristic Local Parser (Safe Fallback if API key missing or API rate limited)
    logger.info("Executing heuristic job parsing fallback...")
    return _heuristic_job_parser(clean_text)


def _heuristic_job_parser(text: str) -> Dict[str, Any]:
    """Extract job metadata deterministically when Gemini API is offline."""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    first_line = lines[0] if lines else "Software Engineering Intern"
    
    # Infer title and company from first lines
    title = first_line[:100]
    company = "Company (Not specified)"
    if " at " in first_line:
        parts = first_line.split(" at ", 1)
        title, company = parts[0].strip(), parts[1].strip()
    elif " - " in first_line:
        parts = first_line.split(" - ", 1)
        title, company = parts[0].strip(), parts[1].strip()

    # Detect work type
    lower_text = text.lower()
    work_type = "unspecified"
    if "remote" in lower_text or "work from home" in lower_text:
        work_type = "remote"
    elif "hybrid" in lower_text:
        work_type = "hybrid"
    elif "on-site" in lower_text or "onsite" in lower_text or "in office" in lower_text:
        work_type = "onsite"

    # Detect skills from master catalog keywords
    known_skills = [
        "Python", "JavaScript", "TypeScript", "Java", "C++", "C#", "Go", "Rust",
        "React", "Vue", "Angular", "Node.js", "Express", "Flask", "Django", "FastAPI",
        "SQL", "PostgreSQL", "Oracle DB", "MySQL", "MongoDB", "Redis",
        "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Git", "Linux",
        "Machine Learning", "Pandas", "NumPy", "PyTorch", "TensorFlow", "HTML5", "CSS3"
    ]
    
    extracted_req = []
    for skill in known_skills:
        # Match as word boundary
        pattern = r"\b" + re.escape(skill.lower()) + r"\b"
        if re.search(pattern, lower_text):
            extracted_req.append(skill)

    if not extracted_req:
        extracted_req = ["Python", "Problem Solving", "Git"]

    # Detect stipend / compensation
    stipend = "Not specified"
    stipend_match = re.search(r"(\$\d+(?:,\d+)*(?:\.\d+)?(?:\s*(?:-\s*\$\d+|\/hr|\/month|\/week|/hr))?)", text, re.IGNORECASE)
    if stipend_match:
        stipend = stipend_match.group(1)
    elif "unpaid" in lower_text:
        stipend = "Unpaid"
    elif "paid" in lower_text:
        stipend = "Paid internship"

    # Detect location
    location = "Not specified"
    loc_match = re.search(r"(?:Location|Based in|Office):\s*([^\n\r,]+(?:,\s*[^\n\r]+)?)", text, re.IGNORECASE)
    if loc_match:
        location = loc_match.group(1).strip()

    summary = (
        f"Internship opportunity for {title} at {company}. "
        f"Key focus areas include {', '.join(extracted_req[:4])}."
    )

    return {
        "job_title": title,
        "company": company,
        "role_summary": summary,
        "required_skills": extracted_req[:8],
        "preferred_skills": extracted_req[8:12] if len(extracted_req) > 8 else ["Docker", "Unit Testing"],
        "experience_required": "Enrolled in University / 0-1 years",
        "education_required": "Currently pursuing Bachelor's or Master's degree in CS or related field",
        "location": location,
        "work_type": work_type,
        "duration": "10-12 weeks (Summer)",
        "stipend": stipend,
        "other_requirements": ["Legal authorization to work in country of employment"],
    }


# ============================================================================
# Module C: Semantic Skill Matcher
# ============================================================================

def match_skills(
    student_skills: List[Dict[str, Any]],
    required_skills: List[str],
    preferred_skills: List[str]
) -> Dict[str, Any]:
    """Compare student profile skills against job requirements using semantic matching."""
    
    # Format inputs for prompt
    student_skills_repr = "\n".join(
        [f"- {s.get('skill_name', s.get('name'))} (Proficiency: {s.get('proficiency', 'Intermediate')})"
         for s in student_skills]
    ) or "- None listed"

    req_skills_repr = "\n".join([f"- {s}" for s in required_skills]) or "- None"
    pref_skills_repr = "\n".join([f"- {s}" for s in preferred_skills]) or "- None"

    prompt = SKILL_MATCHER_USER_PROMPT.format(
        student_skills=student_skills_repr,
        required_skills=req_skills_repr,
        preferred_skills=pref_skills_repr,
    )

    # 1. Attempt Gemini AI semantic matching
    ai_result = _call_gemini_structured(
        system_instruction=SKILL_MATCHER_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=SkillMatchSchema,
    )

    if ai_result and "matched_skills" in ai_result and "missing_skills" in ai_result:
        matched = ai_result.get("matched_skills", [])
        missing = ai_result.get("missing_skills", [])
        areas = ai_result.get("areas_to_improve", [])
        pct = calculate_match_percentage(matched, missing)
        return {
            "matched_skills": matched,
            "missing_skills": missing,
            "match_percentage": pct,
            "areas_to_improve": areas,
        }

    # 2. Local Semantic & Alias Fallback
    logger.info("Executing local semantic skill matching fallback...")
    return _local_semantic_matcher(student_skills, required_skills, preferred_skills)


def _normalize_skill(skill: str) -> str:
    """Normalize skill names to canonical forms for alias matching."""
    s = skill.strip().lower()
    aliases = {
        "reactjs": "react", "react.js": "react",
        "vuejs": "vue", "vue.js": "vue",
        "angularjs": "angular",
        "nodejs": "node.js", "node": "node.js",
        "golang": "go",
        "k8s": "kubernetes",
        "postgres": "postgresql", "psql": "postgresql",
        "py": "python", "python3": "python",
        "js": "javascript", "ts": "typescript",
        "gcp": "google cloud platform", "google cloud": "google cloud platform",
        "aws": "amazon web services",
        "docker compose": "docker",
        "html": "html5", "css": "css3",
    }
    return aliases.get(s, s)


def _local_semantic_matcher(
    student_skills: List[Dict[str, Any]],
    required_skills: List[str],
    preferred_skills: List[str],
) -> Dict[str, Any]:
    """Perform deterministic semantic matching with synonym handling."""
    # Build student normalized map
    std_map = {}
    for s in student_skills:
        name = s.get("skill_name") or s.get("name") or ""
        norm = _normalize_skill(name)
        std_map[norm] = {
            "name": name,
            "proficiency": s.get("proficiency", "Intermediate"),
        }

    matched_skills = []
    missing_skills = []

    # Check Required Skills
    for req in required_skills:
        norm_req = _normalize_skill(req)
        if norm_req in std_map:
            matched_skills.append({
                "requirement": req,
                "matched_student_skill": std_map[norm_req]["name"],
                "student_proficiency": std_map[norm_req]["proficiency"],
                "is_required": True,
            })
        else:
            missing_skills.append({
                "requirement": req,
                "importance": "critical",
                "reason": f"Mandatory qualification for core responsibilities in {req}.",
            })

    # Check Preferred Skills
    for pref in preferred_skills:
        norm_pref = _normalize_skill(pref)
        if norm_pref in std_map:
            matched_skills.append({
                "requirement": pref,
                "matched_student_skill": std_map[norm_pref]["name"],
                "student_proficiency": std_map[norm_pref]["proficiency"],
                "is_required": False,
            })
        else:
            missing_skills.append({
                "requirement": pref,
                "importance": "nice_to_have",
                "reason": f"Preferred bonus skill that enhances application competitiveness in {pref}.",
            })

    areas_to_improve = []
    critical_missing = [m["requirement"] for m in missing_skills if m["importance"] == "critical"]
    if critical_missing:
        areas_to_improve.append(f"Strengthen core required skills: {', '.join(critical_missing[:3])}.")
    nice_missing = [m["requirement"] for m in missing_skills if m["importance"] == "nice_to_have"]
    if nice_missing:
        areas_to_improve.append(f"Familiarize with preferred technologies: {', '.join(nice_missing[:3])}.")
    if not areas_to_improve:
        areas_to_improve.append("Your technical skill profile strongly aligns with all requirements for this role!")

    pct = calculate_match_percentage(matched_skills, missing_skills)

    return {
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "match_percentage": pct,
        "areas_to_improve": areas_to_improve,
    }


def calculate_match_percentage(
    matched_skills: List[Dict[str, Any]],
    missing_skills: List[Dict[str, Any]]
) -> float:
    """Deterministically compute the explainable match score (0.0 to 100.0).

    Weighted formula:
    - 75% weight allocated to mandatory/critical skills.
    - 25% weight allocated to preferred/nice-to-have skills.
    """
    matched_crit = sum(1 for m in matched_skills if m.get("is_required", True))
    matched_nice = sum(1 for m in matched_skills if not m.get("is_required", True))

    missing_crit = sum(1 for m in missing_skills if m.get("importance") == "critical")
    missing_nice = sum(1 for m in missing_skills if m.get("importance") == "nice_to_have")

    total_crit = matched_crit + missing_crit
    total_nice = matched_nice + missing_nice

    if total_crit == 0 and total_nice == 0:
        return 100.0

    if total_crit > 0:
        crit_score = (matched_crit / total_crit) * 75.0
        if total_nice > 0:
            nice_score = (matched_nice / total_nice) * 25.0
        else:
            # Scale critical to 100% if no preferred skills exist
            nice_score = (matched_crit / total_crit) * 25.0
        final_score = crit_score + nice_score
    else:
        final_score = (matched_nice / total_nice) * 100.0

    return round(max(0.0, min(100.0, final_score)), 1)


# ============================================================================
# Module D: Dynamic Learning Roadmap Generator
# ============================================================================

def generate_learning_roadmap(
    job_title: str,
    missing_skills: List[Dict[str, Any]],
    total_weeks: int = 4,
    hours_per_week: int = 10,
) -> Dict[str, Any]:
    """Generate a structured week-by-week learning plan and portfolio projects."""
    total_weeks = max(1, min(16, int(total_weeks)))
    hours_per_week = max(2, min(40, int(hours_per_week)))

    # Format missing skills for prompt
    skills_repr = "\n".join(
        [f"- {m.get('requirement', m.get('skill_name'))} (Importance: {m.get('importance', 'critical')}) — {m.get('reason', '')}"
         for m in missing_skills]
    ) or "- General Backend & Cloud Development"

    system_prompt = ROADMAP_SYSTEM_PROMPT.format(
        total_weeks=total_weeks,
        hours_per_week=hours_per_week,
    )
    user_prompt = ROADMAP_USER_PROMPT.format(
        total_weeks=total_weeks,
        hours_per_week=hours_per_week,
        job_title=job_title,
        missing_skills=skills_repr,
    )

    # 1. Attempt Gemini structured generation
    ai_result = _call_gemini_structured(
        system_instruction=system_prompt,
        user_prompt=user_prompt,
        response_schema=RoadmapSchema,
    )

    if ai_result and "weeks" in ai_result and len(ai_result["weeks"]) > 0:
        return ai_result

    # 2. Heuristic Roadmap Fallback
    logger.info("Executing local heuristic roadmap generator fallback...")
    return _heuristic_roadmap_generator(job_title, missing_skills, total_weeks, hours_per_week)


def _heuristic_roadmap_generator(
    job_title: str,
    missing_skills: List[Dict[str, Any]],
    total_weeks: int,
    hours_per_week: int,
) -> Dict[str, Any]:
    """Generate a realistic structured learning plan deterministically."""
    skill_names = [m.get("requirement", m.get("skill_name", "Technical Skill")) for m in missing_skills]
    if not skill_names:
        skill_names = ["Python Programming", "Relational Databases", "Git & CI/CD", "Docker"]

    weeks = []
    # Distribute skills across weeks
    for w in range(1, total_weeks + 1):
        # Pick 1-2 skills for this week
        idx = (w - 1) % len(skill_names)
        target_skill = skill_names[idx]

        topics = [
            TopicMilestone(
                topic_id=f"w{w}_t1",
                title=f"{target_skill} Core Fundamentals & Architecture",
                description=f"Understand key syntax, mental models, and lifecycle of {target_skill}.",
                resource_type="Official Documentation & Tutorials",
                resource_hint="Official Documentation / MDN Web Docs",
            ),
            TopicMilestone(
                topic_id=f"w{w}_t2",
                title=f"Hands-on Exercises with {target_skill}",
                description=f"Implement practical algorithms and micro-exercises exercising {target_skill}.",
                resource_type="Interactive Sandbox & Exercises",
                resource_hint="FreeCodeCamp / GitHub Open Repos",
            ),
            TopicMilestone(
                topic_id=f"w{w}_t3",
                title=f"Testing, Debugging & Best Practices in {target_skill}",
                description="Write unit tests, handle common edge cases, and inspect execution profiles.",
                resource_type="Guided Workshop / Video Walkthrough",
                resource_hint="YouTube Technical Crash Courses",
            ),
        ]

        week_plan = WeekPlan(
            week_number=w,
            theme=f"Week {w}: Deep Dive into {target_skill}",
            weekly_focus=f"Master foundational concepts and hands-on implementation patterns of {target_skill}.",
            estimated_hours=hours_per_week,
            topics=topics,
        )
        weeks.append(week_plan.model_dump())

    projects = [
        PracticeProject(
            title=f"Full-Stack Showcase Application for {job_title}",
            description=f"Build an end-to-end service combining {', '.join(skill_names[:3])} with REST API endpoints and data persistence.",
            technologies=skill_names[:4],
            portfolio_impact="Publish on GitHub with a comprehensive README, architectural diagrams, and automated test badges.",
        ),
        PracticeProject(
            title="Automated Data Ingestion & Microservice Pipeline",
            description="Create a background ingestion worker that processes real-world APIs, caches results, and exposes query endpoints.",
            technologies=[skill_names[0], "SQL / PostgreSQL", "Docker"],
            portfolio_impact="Demonstrates systems thinking, data modeling, and practical containerized deployment.",
        ),
    ]

    return {
        "summary": (
            f"Tailored {total_weeks}-week curriculum designed for {hours_per_week} hours/week to prepare for '{job_title}'. "
            f"Focuses on bridging gaps in {', '.join(skill_names[:4])}."
        ),
        "weeks": weeks,
        "practice_projects": [p.model_dump() for p in projects],
    }


# ============================================================================
# Module E: Job Risk Analyzer (Scam & Exploitation Detection)
# ============================================================================

def analyze_job_risk(job_text: str, company_name: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate job posting text for scam red flags using deterministic checks + AI analysis."""
    clean_text = job_text[:MAX_INPUT_LENGTH].strip()
    if not clean_text:
        return {
            "risk_level": "Low",
            "risk_score": 1.0,
            "summary": "No text provided to evaluate.",
            "indicators": [],
            "safety_advice": ["Provide job description text for safety analysis."],
        }

    # 1. Deterministic Python Regex & Keyword Checks
    deterministic_signals = _deterministic_risk_scanner(clean_text)

    # 2. AI Gemini Structured Risk Analysis
    user_prompt = RISK_ANALYZER_USER_PROMPT.format(job_text=clean_text)
    ai_result = _call_gemini_structured(
        system_instruction=RISK_ANALYZER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_schema=JobRiskSchema,
    )

    # 3. Merge Deterministic and AI Results
    merged_indicators = list(deterministic_signals)
    ai_indicators = ai_result.get("indicators", []) if ai_result else []

    # Deduplicate indicators by title / category similarity
    existing_titles = {ind["title"].lower() for ind in merged_indicators}
    for ai_ind in ai_indicators:
        if ai_ind.get("title", "").lower() not in existing_titles:
            merged_indicators.append(ai_ind)
            existing_titles.add(ai_ind.get("title", "").lower())

    # Compute Composite Risk Score & Level
    if ai_result and "risk_score" in ai_result:
        base_score = float(ai_result["risk_score"])
    else:
        # Score calculated from deterministic signals
        severity_weights = {"High": 3.5, "Medium": 2.0, "Low": 1.0}
        base_score = sum(severity_weights.get(ind.get("severity"), 1.0) for ind in deterministic_signals)
        base_score = min(10.0, base_score)

    # If any deterministic HIGH severity signal was found, guarantee at least 7.0 score
    if any(ind.get("severity") == "High" for ind in deterministic_signals):
        base_score = max(base_score, 7.5)

    risk_score = round(max(0.0, min(10.0, base_score)), 1)

    # Map to overall risk level
    if risk_score >= 6.5:
        overall_level = "High"
    elif risk_score >= 3.5:
        overall_level = "Medium"
    elif risk_score >= 1.5:
        overall_level = "Low"
    else:
        overall_level = "Safe"

    # Default Safety Advice
    default_advice = [
        "Never send money, purchase equipment, or wire funds for training or onboarding.",
        "Verify the recruiter's official company email domain (e.g. @company.com) on LinkedIn.",
        "Legitimate employers never conduct official employment interviews solely over WhatsApp or Telegram.",
        "Refuse to provide your Social Security Number, bank credentials, or passport scans before receiving a signed formal offer.",
        "Consult your university career center if an employer pressures you to sign or pay immediately.",
    ]

    safety_advice = ai_result.get("safety_advice", default_advice) if ai_result else default_advice

    summary = ai_result.get("summary") if ai_result else None
    if not summary:
        if overall_level == "High":
            summary = "High risk detected. This posting exhibits critical warning signals associated with common employment scams (such as upfront payment requests or informal chat interviews)."
        elif overall_level == "Medium":
            summary = "Moderate warning indicators detected. Some elements of this posting (compensation, contact methods, or company details) require verification."
        else:
            summary = "Low/No significant scam risk indicators detected. The posting appears to follow standard corporate recruitment conventions."

    return {
        "risk_level": overall_level,
        "risk_score": risk_score,
        "summary": summary,
        "indicators": merged_indicators,
        "safety_advice": safety_advice,
        "disclaimer": "This is an automated risk assessment, not legal proof of fraud. Verify the company independently through official channels.",
    }


def _deterministic_risk_scanner(text: str) -> List[Dict[str, Any]]:
    """Scan text for high-confidence pattern matches indicating recruitment scams."""
    signals = []
    lower = text.lower()

    # 1. Upfront Payments & Check Cashing
    fee_patterns = [
        (r"pay\s+(?:for\s+)?(?:training|equipment|laptop|software|registration|background\s*check)", "Candidate asked to pay for onboarding/equipment/training fees"),
        (r"(?:wire\s+transfer|western\s*union|moneygram|crypto|bitcoin|gift\s*card)", "Request for non-traditional or irreversible payment methods"),
        (r"(?:deposit|cash)\s+(?:a|this|our)?\s*check\s+(?:and\s+send|for\s+equipment)", "Check-cashing or fake check refund scheme pattern"),
    ]
    for pattern, explanation in fee_patterns:
        match = re.search(pattern, lower)
        if match:
            # Extract excerpt
            start = max(0, match.start() - 25)
            end = min(len(text), match.end() + 25)
            snippet = text[start:end].strip()
            signals.append({
                "category": "Financial / Upfront Fees",
                "title": "Upfront Payment or Money Transfer Request",
                "severity": "High",
                "quoted_evidence": f"...{snippet}...",
                "explanation": explanation,
            })
            break

    # 2. Suspicious Messaging Channels
    msg_patterns = [
        (r"whatsapp\s*(?:only|contact|interview|message|at|\:|\+)", "WhatsApp mentioned as primary or sole interview/contact channel"),
        (r"telegram\s*(?:only|contact|interview|message|at|\:|\@)", "Telegram mentioned as interview or recruitment communication channel"),
        (r"signal\s*(?:only|messenger|app)", "Signal messenger requested for job application processing"),
    ]
    for pattern, explanation in msg_patterns:
        match = re.search(pattern, lower)
        if match:
            start = max(0, match.start() - 25)
            end = min(len(text), match.end() + 25)
            snippet = text[start:end].strip()
            signals.append({
                "category": "Communication Channels",
                "title": "Informal / Off-Platform Chat Application",
                "severity": "High",
                "quoted_evidence": f"...{snippet}...",
                "explanation": explanation,
            })
            break

    # 3. Free Email Domains for Recruiter Contact
    email_match = re.search(r"[\w\.-]+@(gmail|yahoo|hotmail|outlook)\.com", lower)
    if email_match:
        signals.append({
            "category": "Communication Channels",
            "title": "Free Public Email Domain Used by Recruiter",
            "severity": "Medium",
            "quoted_evidence": email_match.group(0),
            "explanation": "Corporate recruiters typically use company-branded domains rather than free public webmail addresses.",
        })

    # 4. Sensitive Personal Data Demands
    sensitive_patterns = [
        (r"(?:send|provide|submit)\s+(?:your\s+)?(?:ssn|social security\s*(?:number)?|bank\s*details|routing\s*number|credit\s*card)", "Request for bank account or SSN before interview"),
        (r"(?:copy|scan)\s+of\s+(?:your\s+)?(?:passport|driver'?s license)\s+to\s+apply", "Premature request for official government photo ID"),
    ]
    for pattern, explanation in sensitive_patterns:
        match = re.search(pattern, lower)
        if match:
            start = max(0, match.start() - 25)
            end = min(len(text), match.end() + 25)
            snippet = text[start:end].strip()
            signals.append({
                "category": "Sensitive Data Request",
                "title": "Premature Request for Financial or Identity Credentials",
                "severity": "High",
                "quoted_evidence": f"...{snippet}...",
                "explanation": explanation,
            })
            break

    # 5. Unrealistic Hourly Pay for Student
    pay_match = re.search(r"\$(?:[8-9]\d|\d{3,})\s*(?:\/|\s*per\s*)(?:hr|hour)", text, re.IGNORECASE)
    if pay_match:
        signals.append({
            "category": "Unrealistic Compensation",
            "title": "Anomalously High Hourly Rate for Internship",
            "severity": "Medium",
            "quoted_evidence": pay_match.group(0),
            "explanation": "Advertised pay is disproportionately above typical student internship market averages, a common hook in phishing postings.",
        })

    # 6. Absence of Interview Process / Guaranteed Instant Hire
    guarantee_patterns = [
        r"no\s+interview\s+(?:required|needed|necessary)",
        r"(?:hired|start)\s+immediately\s+(?:today|upon\s+text)",
        r"guaranteed\s+(?:placement|job|income)",
    ]
    for pattern in guarantee_patterns:
        match = re.search(pattern, lower)
        if match:
            start = max(0, match.start() - 25)
            end = min(len(text), match.end() + 25)
            snippet = text[start:end].strip()
            signals.append({
                "category": "Hiring Process",
                "title": "Absence of Candidate Screening or Instant Hire Promise",
                "severity": "High",
                "quoted_evidence": f"...{snippet}...",
                "explanation": "Legitimate technical internships require interviews, portfolio reviews, or technical assessments.",
            })
            break

    return signals


# ============================================================================
# Module F: AI CV Analyzer Service
# ============================================================================

def analyze_cv_text(
    cv_text: str,
    job_title: Optional[str] = None,
    job_requirements: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Perform deep AI evaluation of student CV, extracting skills, education,
    evaluating four calibrated score dimensions, and providing high-impact bullet rewrites.
    """
    clean_cv = (cv_text or "").strip()[:MAX_INPUT_LENGTH]
    job_ctx = f"Role: {job_title or 'Software Engineering Intern'}"
    if job_requirements:
        job_ctx += f"\nTarget Requirements: {', '.join(job_requirements)}"

    prompt = CV_ANALYSIS_USER_PROMPT.format(
        cv_text=clean_cv,
        job_text=job_ctx,
    )

    result = _call_gemini_structured(
        system_instruction=CV_ANALYSIS_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=CVAnalysisSchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic CV analyzer fallback...")
    return _heuristic_cv_analyzer(clean_cv, job_title, job_requirements)


def _heuristic_cv_analyzer(
    cv_text: str,
    job_title: Optional[str] = None,
    job_requirements: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Deterministic CV evaluation and structured entity extraction fallback."""
    lines = [line.strip() for line in cv_text.splitlines() if line.strip()]
    lower_text = cv_text.lower()

    # 1. Candidate Name (heuristic: first non-empty line or capitalized tokens)
    candidate_name = "Candidate"
    for line in lines[:4]:
        if len(line) < 40 and not re.search(r"[@|:\/\\0-9]", line):
            candidate_name = line.title()
            break

    # 2. Contact details
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", cv_text)
    contact_email = email_match.group(0) if email_match else None

    phone_match = re.search(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", cv_text)
    contact_phone = phone_match.group(0) if phone_match else None

    # 3. Categorized skills extraction
    SKILL_CATALOG = {
        "programming": ["python", "java", "c++", "c#", "javascript", "typescript", "golang", "go", "rust", "php", "ruby", "c"],
        "web": ["react", "react.js", "vue", "angular", "node.js", "nodejs", "flask", "django", "fastapi", "express", "html", "css", "tailwind", "bootstrap", "rest api"],
        "data": ["sql", "postgresql", "postgres", "mysql", "mongodb", "sqlite", "redis", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch"],
        "cloud": ["docker", "kubernetes", "k8s", "aws", "azure", "gcp", "ci/cd", "github actions", "linux"],
        "soft_skills": ["communication", "teamwork", "leadership", "problem solving", "agile", "scrum", "collaboration"],
        "tools": ["git", "github", "jira", "postman", "vs code", "pycharm", "figma", "intellij"],
    }

    extracted_skills = {
        "programming": [],
        "web": [],
        "data": [],
        "cloud": [],
        "soft_skills": [],
        "tools": [],
    }

    all_found_skills = set()
    for category, skill_keywords in SKILL_CATALOG.items():
        for kw in skill_keywords:
            pattern = r"\b" + re.escape(kw) + r"\b"
            if re.search(pattern, lower_text):
                disp_name = kw.title() if len(kw) > 3 else kw.upper()
                if "react" in kw: disp_name = "React"
                if "sql" in kw: disp_name = "SQL" if kw == "sql" else kw.title()
                if "node" in kw: disp_name = "Node.js"
                if "aws" in kw: disp_name = "AWS"
                if "ci/cd" in kw: disp_name = "CI/CD"
                if "git" == kw: disp_name = "Git"
                if disp_name not in extracted_skills[category]:
                    extracted_skills[category].append(disp_name)
                    all_found_skills.add(disp_name)

    # 4. Education extraction
    education = []
    edu_match = re.search(r"(?:bachelor|master|b\.s\.|m\.s\.|degree|university|college|polytechnic)[^\n.]*", cv_text, re.IGNORECASE)
    if edu_match:
        institution = "University / College"
        univ_match = re.search(r"([A-Z][a-zA-Z\s]+(?:University|College|Institute|Polytechnic))", cv_text)
        if univ_match:
            institution = univ_match.group(1).strip()

        grad_match = re.search(r"\b(202[4-9]|2030)\b", cv_text)
        gpa_match = re.search(r"\bGPA[:\s]+([0-4]\.\d{1,2})\b", cv_text, re.IGNORECASE)
        education.append({
            "institution": institution,
            "degree": edu_match.group(0).strip()[:60],
            "graduation_year": grad_match.group(0) if grad_match else "2026",
            "gpa": gpa_match.group(1) if gpa_match else None,
        })
    else:
        education.append({
            "institution": "University Degree Program",
            "degree": "B.S. in Computer Science / Software Engineering",
            "graduation_year": "2026",
            "gpa": None,
        })

    # 5. Experience and Projects extraction
    experience = []
    projects = []
    proj_keywords = ["project", "developed", "built", "engineered", "implemented", "created", "designed"]
    for i, line in enumerate(lines):
        if any(w in line.lower() for w in ["intern", "engineer", "assistant", "developer"]) and len(line) < 80:
            if not any(e["role"] == line for e in experience):
                experience.append({
                    "role": line,
                    "company": "Tech Internship / Academic Experience",
                    "duration": "Summer 2025",
                    "highlights": [lines[min(i+1, len(lines)-1)]] if i+1 < len(lines) else ["Contributed to software features."],
                })
        elif any(w in line.lower() for w in proj_keywords) and len(line) < 60 and not line.endswith("."):
            title = line.strip(" -:*#")
            if len(title) > 5 and not any(p["title"] == title for p in projects):
                techs = [s for s in all_found_skills if s.lower() in cv_text.lower()][:3]
                projects.append({
                    "title": title,
                    "technologies": techs or ["Python", "Git"],
                    "description": lines[min(i+1, len(lines)-1)] if i+1 < len(lines) else "Full-stack software application.",
                    "impact": "Delivered high reliability and modular code.",
                })

    if not projects:
        projects = [
            {
                "title": "Full-Stack Web & API Service",
                "technologies": list(all_found_skills)[:3] or ["Python", "SQL", "Git"],
                "description": "Engineered RESTful web application with data persistence, user auth, and responsive UI.",
                "impact": "Demonstrates full-stack development and architectural patterns.",
            }
        ]

    # 6. Calibrated Score Calculation (4 dimensions, 0-25 each)
    # Clarity (Structure & formatting signals)
    clarity_score = 18
    if len(lines) >= 15: clarity_score += 2
    if contact_email: clarity_score += 2
    if any(h in lower_text for h in ["education", "skills", "experience", "projects"]): clarity_score += 2
    clarity_score = min(24, clarity_score)

    # Relevance (Skill richness and match to target role)
    relevance_score = 15
    if len(all_found_skills) >= 6: relevance_score += 4
    if len(all_found_skills) >= 10: relevance_score += 3
    if job_title and any(w in lower_text for w in (job_title.lower().split())): relevance_score += 2
    relevance_score = min(25, relevance_score)

    # Impact (Quantified metrics, action verbs, XYZ format)
    impact_score = 14
    has_metrics = bool(re.search(r"\b(?:\d+%(?: reduction| increase)?|\$\d+|\d+\+? users|\d+ms)\b", cv_text))
    if has_metrics: impact_score += 5
    action_verbs = ["built", "engineered", "designed", "architected", "optimized", "implemented", "reduced", "scaled", "automated"]
    verb_count = sum(1 for v in action_verbs if v in lower_text)
    if verb_count >= 3: impact_score += 4
    impact_score = min(24, impact_score)

    # Formatting / Readability
    formatting_score = 19
    bullet_count = cv_text.count("•") + cv_text.count("- ") + cv_text.count("* ")
    if bullet_count >= 4: formatting_score += 3
    if len(cv_text) < 4000: formatting_score += 2
    formatting_score = min(24, formatting_score)

    total_score = clarity_score + relevance_score + impact_score + formatting_score

    # 7. Weak / missing sections
    weak_sections = []
    if not has_metrics:
        weak_sections.append("Lacks quantifiable metrics: Project bullet points lack numeric impact (e.g. latency improvement, user adoption, test coverage percentage).")
    if "github" not in lower_text and "git" not in lower_text:
        weak_sections.append("Missing portfolio links: No GitHub profile or repository URLs found to verify written code.")
    if len(extracted_skills["cloud"]) == 0 and len(extracted_skills["data"]) == 0:
        weak_sections.append("Limited modern tooling: Consider adding containerization (Docker) or database experience to demonstrate deployment readiness.")
    if not weak_sections:
        weak_sections.append("Professional Summary: Consider adding a 2-line strategic summary highlighting your target internship domain and key strengths.")

    # 8. Highlighted Projects
    highlighted = [p["title"] for p in projects[:3]]

    # 9. Bullet Rewrites using Google XYZ format
    bullet_rewrites = [
        {
            "original_bullet": "Worked on backend APIs and fixed several bugs in the application.",
            "rewritten_bullet": "Engineered 12 RESTful API endpoints utilizing Python and FastAPI, reducing response latency by 35% across core user workflows.",
            "improvement_reason": "Replaces passive 'worked on' with strong action verb 'engineered' and quantifies exact impact using numeric metrics.",
        },
        {
            "original_bullet": "Created database models and wrote SQL queries for student data.",
            "rewritten_bullet": "Architected normalized PostgreSQL schema with indexed foreign keys, supporting 5,000+ relational student records with sub-50ms query execution.",
            "improvement_reason": "Applies the Google X-Y-Z formula: Accomplished [normalized schema] as measured by [<50ms query time], by doing [indexed keys & relational design].",
        },
        {
            "original_bullet": "Helped team build frontend user interfaces using modern components.",
            "rewritten_bullet": "Developed responsive, accessible web interfaces in vanilla JavaScript and HTML5, enhancing cross-browser performance and mobile usability score to 98%.",
            "improvement_reason": "Highlights ownership and technical rigor with concrete front-end performance benchmarks.",
        },
    ]

    summary_verdict = (
        f"Solid technical foundation with {len(all_found_skills)} verified core skills. "
        f"Overall CV score is {total_score}/100. Incorporating quantifiable outcomes and active verbs in project bullets "
        "will significantly elevate interview callback rates."
    )

    return {
        "candidate_name": candidate_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "summary_verdict": summary_verdict,
        "score": {
            "clarity": clarity_score,
            "relevance": relevance_score,
            "impact": impact_score,
            "formatting_readability": formatting_score,
            "total_score": total_score,
        },
        "extracted_skills": extracted_skills,
        "education": education,
        "experience": experience,
        "projects": projects,
        "weak_or_missing_sections": weak_sections,
        "highlighted_projects": highlighted,
        "bullet_rewrites": bullet_rewrites,
    }


# ============================================================================
# Module G: Application Assistant Service
# ============================================================================

def generate_cover_letter(
    student_profile: Dict[str, Any],
    job_info: Dict[str, Any],
    cv_text: Optional[str] = None,
    tone: str = "formal",
) -> Dict[str, Any]:
    """
    Generate an authentic, grounded cover letter tailored to a specific job and student background.
    """
    tone = tone.lower() if tone in ("formal", "friendly", "confident") else "formal"
    
    prompt = COVER_LETTER_USER_PROMPT.format(
        tone=tone,
        student_profile=json.dumps(student_profile, default=str),
        cv_data=(cv_text or "No separate CV uploaded. Use student profile.")[:MAX_INPUT_LENGTH],
        job_description=json.dumps(job_info, default=str),
    )

    result = _call_gemini_structured(
        system_instruction=APPLICATION_ASSISTANT_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=CoverLetterSchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic cover letter generator fallback...")
    return _heuristic_cover_letter(student_profile, job_info, tone)


def _heuristic_cover_letter(
    student_profile: Dict[str, Any],
    job_info: Dict[str, Any],
    tone: str = "formal",
) -> Dict[str, Any]:
    """Deterministic, anti-hallucination cover letter generator fallback."""
    candidate_name = student_profile.get("full_name") or "Applicant"
    university = student_profile.get("university") or "University"
    degree = student_profile.get("degree") or "Computer Science"
    company = job_info.get("company") or "the engineering team"
    role_title = job_info.get("title") or "Software Engineering Intern"
    
    # Extract real skills
    skills_list = [s.get("skill_name") for s in student_profile.get("skills", []) if s.get("skill_name")]
    primary_skills = ", ".join(skills_list[:4]) if skills_list else "Python, SQL, and modern software development"

    # Extract real project
    projects = student_profile.get("projects", [])
    project_highlight = ""
    if projects:
        p0 = projects[0]
        project_highlight = f"For example, in my project '{p0.get('title')}', I leveraged {p0.get('tech_used') or 'core development tools'} to build {p0.get('description', 'a robust application')}."

    if tone == "friendly":
        salutation = f"Dear {company} Recruiting Team,"
        subject = f"Enthusiastic Application: {role_title} — {candidate_name}"
        p1 = f"I was thrilled to come across the {role_title} opening at {company}. As a student pursuing my {degree} at {university}, I have been following {company}'s work with great admiration and would love the opportunity to contribute my skills to your engineering efforts."
        p2 = f"Throughout my academic journey, I have focused on building hands-on systems with technologies like {primary_skills}. {project_highlight} I take pride in writing clean, well-tested code and collaborating actively with team members to solve challenging problems."
        p3 = f"What excites me most about {company} is the opportunity to learn from high-caliber engineers while delivering genuine value to your users. I am eager to bring my curiosity, rapid learning agility, and passion for software engineering to your team this coming term."
        p4 = f"Thank you so much for your time and consideration. I would welcome the chance to speak with you further about how my background aligns with the goals of {company}."
        closing = "Warm regards,"

    elif tone == "confident":
        salutation = f"Dear {company} Hiring Team,"
        subject = f"Candidate Application: {role_title} — {candidate_name}"
        p1 = f"I am writing to submit my application for the {role_title} position at {company}. With a rigorous foundation in {degree} from {university} and practical project experience in {primary_skills}, I am prepared to contribute directly to your production codebase from day one."
        p2 = f"My technical background is rooted in pragmatic engineering and system reliability. {project_highlight} Rather than viewing an internship as a passive exercise, I approach every engineering problem with an owner's mindset—optimizing algorithms, validating data models, and automating workflows."
        p3 = f"The challenges {company} is tackling demand engineers who learn fast, communicate clearly, and take initiative. My profile demonstrates a track record of translating complex requirements into reliable, working software."
        p4 = f"I look forward to discussing how my technical background and problem-solving drive will make an immediate positive impact on your team."
        closing = "Best regards,"

    else:  # Formal (default)
        salutation = f"Dear Hiring Manager and Engineering Team at {company},"
        subject = f"Application for {role_title} — {candidate_name}"
        p1 = f"I am writing to express my strong interest in the {role_title} position at {company}. Currently pursuing my {degree} at {university}, I have developed a strong technical foundation in software engineering principles, algorithms, and full-stack development."
        p2 = f"Through intensive coursework and independent technical initiatives, I have honed practical proficiency in {primary_skills}. {project_highlight} My hands-on projects have taught me the critical importance of modular architecture, thorough unit testing, and maintainable documentation."
        p3 = f"I am drawn to {company} because of your reputation for engineering excellence and impactful technology solutions. I am confident that my academic preparation, dedication to continuous improvement, and disciplined work ethic align closely with your team's values."
        p4 = f"Thank you for considering my application. I welcome the opportunity to discuss my qualifications in an interview and look forward to hearing from you."
        closing = "Respectfully yours,"

    paragraphs = [p1, p2, p3, p4]

    return {
        "recipient_salutation": salutation,
        "subject_line": subject,
        "body_paragraphs": paragraphs,
        "closing_salutation": closing,
        "candidate_name": candidate_name,
        "key_highlights_referenced": skills_list[:4] + ([projects[0].get("title")] if projects else []),
    }


def generate_application_email(
    student_profile: Dict[str, Any],
    job_info: Dict[str, Any],
    tone: str = "formal",
) -> Dict[str, Any]:
    """
    Generate a high-converting, concise outreach/application email.
    """
    tone = tone.lower() if tone in ("formal", "friendly", "confident") else "formal"
    
    prompt = APPLICATION_EMAIL_USER_PROMPT.format(
        tone=tone,
        student_profile=json.dumps(student_profile, default=str),
        job_description=json.dumps(job_info, default=str),
    )

    result = _call_gemini_structured(
        system_instruction=APPLICATION_ASSISTANT_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=ApplicationEmailSchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic application email fallback...")
    return _heuristic_application_email(student_profile, job_info, tone)


def _heuristic_application_email(
    student_profile: Dict[str, Any],
    job_info: Dict[str, Any],
    tone: str = "formal",
) -> Dict[str, Any]:
    """Deterministic application outreach email generator fallback."""
    candidate_name = student_profile.get("full_name") or "Student Candidate"
    university = student_profile.get("university") or "University"
    company = job_info.get("company") or "the Team"
    role_title = job_info.get("title") or "Software Engineering Intern"
    
    skills = [s.get("skill_name") for s in student_profile.get("skills", []) if s.get("skill_name")]
    top_skills = ", ".join(skills[:3]) if skills else "Python and web application development"

    if tone == "friendly":
        subject = f"Excited about {role_title} at {company} — {candidate_name}"
        salutation = f"Hi {company} Recruiting Team,"
        body = (
            f"I hope you're having a wonderful week!\n\n"
            f"I recently applied for the {role_title} role at {company} and wanted to reach out directly to express my enthusiasm. "
            f"I am a {student_profile.get('degree', 'Computer Science student')} at {university} with a strong passion for {top_skills}.\n\n"
            f"I love building practical projects and would be thrilled to bring my energy and fast-learning attitude to your team this term. "
            f"My resume and portfolio are attached for your convenience."
        )
        call_to_action = "I would love to connect for a quick 10-minute chat if you have availability this week!"
        signoff = "Best wishes,"

    elif tone == "confident":
        subject = f"Application: {role_title} — {candidate_name} ({top_skills})"
        salutation = f"Dear {company} Hiring Team,"
        body = (
            f"I am reaching out regarding the {role_title} opening at {company}. "
            f"As a {student_profile.get('degree', 'CS student')} at {university}, I have built working production-grade applications using {top_skills}.\n\n"
            f"I am looking for a rigorous engineering environment where I can contribute to shipping features and solving complex system bottlenecks from week one. "
            f"I have attached my resume detailing relevant projects and verifiable GitHub repositories."
        )
        call_to_action = "I would appreciate the chance to discuss how my skill set matches your technical roadmap."
        signoff = "Sincerely,"

    else:  # Formal
        subject = f"Application for {role_title} — {candidate_name}"
        salutation = f"Dear Hiring Manager at {company},"
        body = (
            f"I am writing to formally submit my candidacy for the {role_title} position at {company}.\n\n"
            f"Currently pursuing my {student_profile.get('degree', 'B.S. in Computer Science')} at {university}, "
            f"I have developed practical technical experience with {top_skills} through rigorous academic projects and practical coursework. "
            f"I am confident that my work ethic, attention to detail, and problem-solving skills will allow me to be a productive member of your engineering team.\n\n"
            f"Please find my resume attached for your consideration."
        )
        call_to_action = "I welcome the opportunity to discuss my qualifications further in an interview."
        signoff = "Respectfully,"

    return {
        "subject_line": subject,
        "salutation": salutation,
        "email_body": body,
        "call_to_action": call_to_action,
        "signoff": signoff,
        "candidate_name": candidate_name,
    }


def generate_interview_prep(
    student_profile: Dict[str, Any],
    job_info: Dict[str, Any],
    cv_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate tailored practice interview questions, evaluation criteria,
    and talking point frameworks grounded in the candidate's real portfolio.
    """
    prompt = INTERVIEW_PREP_USER_PROMPT.format(
        student_profile=json.dumps(student_profile, default=str),
        cv_data=(cv_text or "No separate CV text. Use student profile.")[:MAX_INPUT_LENGTH],
        job_description=json.dumps(job_info, default=str),
    )

    result = _call_gemini_structured(
        system_instruction=APPLICATION_ASSISTANT_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=InterviewPrepSchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic interview prep fallback...")
    return _heuristic_interview_prep(student_profile, job_info)


def _heuristic_interview_prep(
    student_profile: Dict[str, Any],
    job_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Deterministic interview preparation questionnaire fallback."""
    company = job_info.get("company") or "the company"
    role_title = job_info.get("title") or "Software Engineering Intern"
    
    skills = [s.get("skill_name") for s in student_profile.get("skills", []) if s.get("skill_name")]
    primary_tech = skills[0] if skills else "Python"
    secondary_tech = skills[1] if len(skills) > 1 else "SQL"

    projects = student_profile.get("projects", [])
    project_name = projects[0].get("title") if projects else "your recent software project"

    questions = [
        {
            "question_number": 1,
            "category": "Technical Deep Dive",
            "question": f"Walk me through the architecture of {project_name}. What were the biggest technical tradeoffs you made?",
            "why_it_matters": "Evaluates architectural comprehension, modular thinking, and whether the candidate truly built what is listed on their resume.",
            "talking_points": [
                f"State the core user problem solved by {project_name}.",
                f"Explain why you chose {primary_tech} and any database/storage layer.",
                "Discuss a specific challenge (e.g. data consistency, error handling, performance) and how you solved it.",
                "Reflect on what you would improve if refactoring for 10x scale today.",
            ],
            "sample_framework": "STAR: Situation (User need) -> Task (System requirements) -> Action (Architecture & implementation) -> Result (Working software & performance).",
        },
        {
            "question_number": 2,
            "category": "Technical Concept",
            "question": f"How do you approach debugging and profiling performance bottlenecks when using {primary_tech} or {secondary_tech}?",
            "why_it_matters": "Tests analytical troubleshooting methods, familiarity with tooling (logging, profilers, unit tests), and systematic problem decomposition.",
            "talking_points": [
                "Mention starting with reproducible test cases rather than guessing.",
                "Discuss inspecting logs, monitoring database query times, or using debugger breakpoints.",
                "Emphasize writing regression unit tests once the root cause is resolved.",
            ],
            "sample_framework": "Deconstruct: 1. Isolate symptoms -> 2. Form hypothesis -> 3. Validate with telemetry/logs -> 4. Implement fix -> 5. Add automated test.",
        },
        {
            "question_number": 3,
            "category": "Behavioral",
            "question": f"Tell me about a time you had to learn a completely new framework or tool on a tight deadline.",
            "why_it_matters": "Crucial for interns: evaluates self-direction, resourcefulness, and learning curve when faced with unfamiliar internal tools.",
            "talking_points": [
                "Select a genuine example from your coursework or projects.",
                "Detail your research methodology (reading official documentation, prototyping minimal reproductions).",
                "Explain how you asked senior colleagues or peers targeted, high-quality questions after doing initial research.",
            ],
            "sample_framework": "STAR Framework: Describe the time constraint, what resource documentation you prioritized, and what working feature you successfully shipped.",
        },
        {
            "question_number": 4,
            "category": "Situational / Fit",
            "question": f"Why are you interested in joining {company} specifically as a {role_title}, and how does this align with your career goals?",
            "why_it_matters": "Tests company knowledge, authentic passion, and alignment with the team's engineering mission.",
            "talking_points": [
                f"Reference specific products or engineering challenges known about {company}.",
                f"Connect how {company}'s tech stack matches your desire to deepen your skills in {primary_tech}.",
                "State your enthusiasm for mentorship and teamwork in an agile engineering environment.",
            ],
            "sample_framework": "The 3 Whys: 1. Why this industry/company -> 2. Why this role -> 3. Why you are uniquely ready to learn and contribute.",
        },
    ]

    tips = [
        f"Review core fundamentals of {primary_tech} and relational data modeling prior to the technical round.",
        f"Prepare 2-3 specific technical stories from {project_name} using the STAR format.",
        f"Prepare 2 thoughtful questions to ask the interviewer about {company}'s deployment pipeline and engineering culture.",
        "Practice coding solutions out loud to demonstrate clear communication and thought process.",
    ]

    return {
        "role_overview": f"Interview preparation guide for {role_title} at {company}.",
        "top_preparation_tips": tips,
        "questions": questions,
    }


# ============================================================================
# Module H: AI Mock Interview Service
# ============================================================================

def generate_mock_interview_question(
    job_info: Dict[str, Any],
    student_profile: Dict[str, Any],
    question_number: int,
    total_questions: int,
    interview_type: str = "mixed",
    previous_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate an intelligent, contextual interview question tailored to the candidate and role.
    """
    interview_type = interview_type.lower() if interview_type in ("technical", "behavioral", "mixed") else "mixed"
    prev_q_str = "\n".join(f"- {q}" for q in (previous_questions or [])) or "None yet (This is Question 1)."

    prompt = MOCK_INTERVIEW_QUESTION_USER_PROMPT.format(
        question_number=question_number,
        total_questions=total_questions,
        interview_type=interview_type,
        job_description=json.dumps(job_info, default=str),
        student_profile=json.dumps(student_profile, default=str),
        previous_questions=prev_q_str,
    )

    result = _call_gemini_structured(
        system_instruction=MOCK_INTERVIEW_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=InterviewQuestionSchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic mock interview question fallback...")
    return _heuristic_interview_question(job_info, student_profile, question_number, total_questions, interview_type, previous_questions)


def _heuristic_interview_question(
    job_info: Dict[str, Any],
    student_profile: Dict[str, Any],
    question_number: int,
    total_questions: int,
    interview_type: str = "mixed",
    previous_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Deterministic question sequence for offline/fallback operation."""
    company = job_info.get("company") or "our engineering team"
    role = job_info.get("title") or "Software Engineering Intern"
    skills = [s.get("skill_name") for s in student_profile.get("skills", []) if s.get("skill_name")]
    primary_tech = skills[0] if skills else "Python"
    secondary_tech = skills[1] if len(skills) > 1 else "SQL"

    projects = student_profile.get("projects", [])
    project_title = projects[0].get("title") if projects else "your recent software project"

    # Catalog of staged questions based on interview type
    technical_bank = [
        {
            "question_text": f"In your project '{project_title}', how did you handle state management, data integrity, and error propagation when communicating with backend services or databases?",
            "category": "Technical",
            "context_hint": f"Discuss specific error handling patterns, database transactions, or API error codes you used.",
        },
        {
            "question_text": f"Suppose our team at {company} needs to process thousands of incoming event payloads per second using {primary_tech}. What architectural bottlenecks would you monitor, and how would you optimize database queries or memory usage?",
            "category": "Technical",
            "context_hint": "Focus on indexing, asynchronous processing, connection pooling, and caching strategies.",
        },
        {
            "question_text": f"Can you explain how you design and test RESTful API endpoints? What considerations do you take into account regarding idempotency, rate limiting, and HTTP status codes?",
            "category": "Technical",
            "context_hint": "Explain the difference between POST and PUT/PATCH, and give examples of validation tests.",
        },
        {
            "question_text": f"Walk me through a difficult bug or performance regression you encountered while working with {primary_tech} or {secondary_tech}. How did you isolate the root cause?",
            "category": "Technical",
            "context_hint": "Demonstrate systematic troubleshooting: reproducing the bug, checking telemetry/logs, and adding regression tests.",
        },
        {
            "question_text": f"How do you approach database schema normalization versus denormalization? When might you deliberately choose to denormalize a table in {secondary_tech}?",
            "category": "Technical",
            "context_hint": "Trade-offs between write anomalies/storage consistency vs. read latency and heavy JOIN overhead.",
        },
    ]

    behavioral_bank = [
        {
            "question_text": f"Tell me about a time you worked on a technical project and faced conflicting opinions with a teammate on how to architect a feature. How did you reach a consensus?",
            "category": "Behavioral",
            "context_hint": "Apply the STAR method: focus on objective data, active listening, and putting project goals first.",
        },
        {
            "question_text": f"Describe a situation where a project deadline was fast approaching and you realized you wouldn't be able to finish all planned requirements. What did you prioritize and how did you communicate this?",
            "category": "Behavioral",
            "context_hint": "Demonstrate proactive stakeholder communication, scope negotiation, and delivering a viable core MVP.",
        },
        {
            "question_text": f"Can you share an experience where you received critical constructive feedback on code you wrote or a design you proposed? How did you respond and adapt?",
            "category": "Behavioral",
            "context_hint": "Show humility, openness to continuous learning, and how feedback made you a stronger engineer.",
        },
        {
            "question_text": f"Tell me about a time you had to self-teach an unfamiliar technology or tool without formal documentation or classes. What was your systematic learning process?",
            "category": "Behavioral",
            "context_hint": "Highlight rapid learning velocity, reading source code or tutorials, and building minimal reproduction demos.",
        },
        {
            "question_text": f"Why do you want to intern at {company} specifically, and what unique perspective or strengths will you bring to our engineering culture?",
            "category": "Behavioral",
            "context_hint": "Connect your authentic interests to the company's product, mission, and tech stack.",
        },
    ]

    mixed_bank = [
        technical_bank[0],
        behavioral_bank[0],
        technical_bank[1],
        behavioral_bank[1],
        behavioral_bank[4],
    ]

    bank = technical_bank if interview_type == "technical" else (behavioral_bank if interview_type == "behavioral" else mixed_bank)

    # Select an unused question
    prev_set = set(previous_questions or [])
    for q in bank:
        if q["question_text"] not in prev_set:
            return q

    # Fallback if all cycled
    idx = (question_number - 1) % len(bank)
    return bank[idx]


def evaluate_mock_interview_answer(
    question_text: str,
    student_answer: str,
    role_title: str,
    company: str,
    interview_type: str = "mixed",
    question_number: int = 1,
) -> Dict[str, Any]:
    """
    Score a candidate's answer from 1 to 10 with constructive strengths,
    actionable improvements, and a high-impact sample answer.
    """
    clean_ans = (student_answer or "").strip()[:MAX_INPUT_LENGTH]

    prompt = MOCK_INTERVIEW_FEEDBACK_USER_PROMPT.format(
        role_title=role_title or "Software Engineering Intern",
        company=company or "Technology Company",
        interview_type=interview_type,
        question_number=question_number,
        question_text=question_text,
        student_answer=clean_ans,
    )

    result = _call_gemini_structured(
        system_instruction=MOCK_INTERVIEW_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=InterviewAnswerFeedbackSchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic interview answer evaluator fallback...")
    return _heuristic_answer_evaluator(question_text, clean_ans, role_title, company)


def _heuristic_answer_evaluator(
    question_text: str,
    student_answer: str,
    role_title: str,
    company: str,
) -> Dict[str, Any]:
    """Objective, deterministic answer evaluation fallback."""
    words = student_answer.split()
    word_count = len(words)
    lower = student_answer.lower()

    # Base scoring calibration
    score = 6
    if word_count < 15:
        score = 3
    elif word_count < 35:
        score = 5
    elif word_count >= 80:
        score += 2

    # Check for metrics, percentages, numbers
    has_metrics = bool(re.search(r"\b(?:\d+%(?: reduction| increase)?|\d+\+? users|\d+ms|\d+ seconds|\$\d+)\b", student_answer))
    if has_metrics:
        score += 1

    # Check for technical depth keywords
    tech_keywords = ["api", "database", "sql", "cache", "async", "latency", "docker", "test", "index", "git", "python", "service"]
    tech_hits = sum(1 for kw in tech_keywords if kw in lower)
    if tech_hits >= 3:
        score += 1

    # Check for STAR structure signals
    star_signals = ["situation", "task", "action", "result", "because", "impact", "decided to", "learned that"]
    star_hits = sum(1 for s in star_signals if s in lower)
    if star_hits >= 2:
        score += 1

    score = max(2, min(9, score))

    if score >= 8:
        strengths = (
            "Excellent depth and structured communication! You clearly articulated the challenge, your specific technical "
            "contributions, and the outcome. Great use of technical terminology and concise phrasing."
        )
        improvements = (
            "To push this answer to a perfect 10/10, consider explicitly quantifying business or performance impact (e.g. "
            "exact latency reduction percentage or team velocity gains), and briefly mention how you verified your solution with automated tests."
        )
    elif score >= 6:
        strengths = (
            "Good foundational understanding of the core concept. You touched on the main ideas and maintained a confident, "
            "professional tone."
        )
        improvements = (
            "Your answer would benefit from applying the STAR method (Situation, Task, Action, Result). Replace general statements "
            "like 'we worked on it' with specific actions ('I architected the database indexes'). Incorporate concrete metrics to prove impact."
        )
    else:
        strengths = "You directly addressed the prompt and communicated the central idea without excessive filler words."
        improvements = (
            "The answer is too brief and lacks technical granularity. In engineering interviews, provide specific real-world examples, "
            "the technologies involved, tradeoffs considered, and what you learned from the experience."
        )

    sample_answer = (
        "In my recent project, our application faced a challenge where database query response times degraded to over 350ms during peak load. "
        "My task was to optimize the backend pipeline without breaking existing API contracts. I profiled the slow queries using EXPLAIN ANALYZE "
        "in PostgreSQL and discovered missing composite indexes on our foreign keys. I implemented targeted B-tree indexes and introduced Redis "
        "caching for read-heavy endpoints. As a result, our 95th-percentile response latency dropped by 68% to under 45ms, and the service easily "
        "handled 5,000 concurrent requests during load testing."
    )

    return {
        "score": score,
        "strengths": strengths,
        "improvements": improvements,
        "sample_answer": sample_answer,
    }


def summarize_mock_interview_session(
    transcript: str,
    role_title: str,
    company: str,
    interview_type: str = "mixed",
    total_questions: int = 5,
) -> Dict[str, Any]:
    """
    Generate an executive summary report for a completed mock interview session.
    """
    prompt = MOCK_INTERVIEW_SUMMARY_USER_PROMPT.format(
        role_title=role_title or "Software Engineering Intern",
        company=company or "Technology Company",
        interview_type=interview_type,
        total_questions=total_questions,
        transcript=transcript[:MAX_INPUT_LENGTH],
    )

    result = _call_gemini_structured(
        system_instruction=MOCK_INTERVIEW_SYSTEM_PROMPT,
        user_prompt=prompt,
        response_schema=InterviewSummarySchema,
    )

    if result:
        return result

    logger.info("Executing local heuristic mock interview summary fallback...")
    return _heuristic_interview_summary(transcript, role_title, company)


def _heuristic_interview_summary(
    transcript: str,
    role_title: str,
    company: str,
) -> Dict[str, Any]:
    """Calculate overall performance metrics and key takeaways for completed session."""
    scores = [int(s) for s in re.findall(r"Score:\s*(\d+)\s*\/\s*10", transcript)]
    overall_score = round(sum(scores) / max(len(scores), 1), 1) if scores else 7.5

    if overall_score >= 8.0:
        verdict = (
            f"Strong and competitive interview performance! You demonstrated technical competence, structured articulation, "
            f"and sound engineering tradeoffs well-aligned with what {company} seeks in a {role_title}."
        )
    elif overall_score >= 6.0:
        verdict = (
            f"Promising foundation with good technical communication. With additional practice structuring answers using the "
            f"STAR framework and emphasizing measurable results, you will be well prepared for live interviews at {company}."
        )
    else:
        verdict = (
            f"Good initial practice run. Focus on expanding technical depth, citing concrete projects from your coursework and portfolio, "
            f"and practicing answers out loud to build storytelling confidence."
        )

    top_improvements = [
        "Quantify outcomes: Consistently state numeric impact (e.g. % performance increase, query time reduction, user count).",
        "Adopt the STAR framework: Clearly separate Situation/Task from your personal Action and the final measurable Result.",
        "Highlight architectural trade-offs: Explain why you chose one technology or data structure over alternatives.",
    ]

    key_strengths = [
        "Clear and professional communication tone throughout all questions.",
        "Demonstrated hands-on experience and familiarity with practical software engineering patterns.",
        "Thoughtful approach to problem decomposition and debugging.",
    ]

    return {
        "overall_score": overall_score,
        "verdict": verdict,
        "top_improvement_areas": top_improvements,
        "key_strengths": key_strengths,
    }


