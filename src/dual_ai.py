"""
Dual-AI Module for flyto-indexer MCP Server

Provides multi-model collaboration, specialized agents, and consensus mode.
Integrates with flyto-pro VPS for AI coordination.

Architecture:
- Claude Code calls MCP tools
- MCP tools call VPS API or local LLM
- VPS coordinates GPT + Claude for Dual-AI tasks
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# VPS API Configuration
VPS_API_URL = os.getenv("FLYTO_VPS_URL", "https://api.flyto2.net/api/v1/pro")
VPS_TIMEOUT = int(os.getenv("FLYTO_VPS_TIMEOUT", "60"))


class AgentRole(Enum):
    """Available agent roles in the Dual-AI system."""
    PLANNER = "planner"
    EXECUTOR = "executor"
    REVIEWER = "reviewer"
    SECURITY = "security"
    PERFORMANCE = "performance"
    TEST_GENERATOR = "test_generator"


class ConsensusMode(Enum):
    """Consensus voting modes."""
    MAJORITY = "majority"      # >50% agree
    UNANIMOUS = "unanimous"    # 100% agree
    WEIGHTED = "weighted"      # Weighted by expertise


class ReviewType(Enum):
    """Code review types."""
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    ALL = "all"


@dataclass
class AgentInfo:
    """Information about an available agent."""
    id: str
    name: str
    model: str
    capabilities: List[str]
    status: str = "available"


# Available agents definition
AVAILABLE_AGENTS = [
    AgentInfo(
        id="planner",
        name="Task Planner",
        model="gpt-4o",
        capabilities=["planning", "decomposition", "task_analysis"],
    ),
    AgentInfo(
        id="executor",
        name="Code Executor",
        model="claude-opus-4.5",
        capabilities=["code_generation", "implementation", "debugging"],
    ),
    AgentInfo(
        id="reviewer",
        name="Code Reviewer",
        model="claude-opus-4.5",
        capabilities=["code_review", "best_practices", "suggestions"],
    ),
    AgentInfo(
        id="security",
        name="Security Reviewer",
        model="claude-opus-4.5",
        capabilities=["vulnerability_scan", "code_audit", "security_analysis"],
    ),
    AgentInfo(
        id="performance",
        name="Performance Optimizer",
        model="gpt-4o",
        capabilities=["performance_analysis", "optimization", "profiling"],
    ),
    AgentInfo(
        id="test_generator",
        name="Test Generator",
        model="claude-opus-4.5",
        capabilities=["test_generation", "coverage_analysis", "edge_cases"],
    ),
]


async def call_vps(endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Call VPS API with error handling.

    Args:
        endpoint: API endpoint path (without base URL)
        data: Request JSON data

    Returns:
        Response JSON dict
    """
    headers = {
        "Content-Type": "application/json",
        "X-User-Id": "mcp-client",  # Required by VPS auth
    }

    try:
        import httpx

        async with httpx.AsyncClient(timeout=VPS_TIMEOUT) as client:
            response = await client.post(
                f"{VPS_API_URL}/{endpoint}",
                json=data,
                headers=headers
            )
            response.raise_for_status()
            return response.json()

    except ImportError:
        # Fallback to requests if httpx not available
        import requests

        response = requests.post(
            f"{VPS_API_URL}/{endpoint}",
            json=data,
            headers=headers,
            timeout=VPS_TIMEOUT
        )
        response.raise_for_status()
        return response.json()

    except Exception as e:
        logger.error(f"VPS API call failed: {e}")
        return {"ok": False, "error": str(e)}


async def call_claude_local(prompt: str, system: str = "") -> str:
    """
    Call Claude API directly for local operations.
    Uses Anthropic SDK.

    Args:
        prompt: User prompt
        system: System prompt

    Returns:
        Claude's response text
    """
    try:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "Error: ANTHROPIC_API_KEY not configured"

        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system if system else "You are a helpful AI assistant.",
            messages=[{"role": "user", "content": prompt}]
        )

        return message.content[0].text

    except ImportError:
        return "Error: anthropic SDK not installed"
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return f"Error: {str(e)}"


async def call_openai_local(prompt: str, system: str = "") -> str:
    """
    Call OpenAI API directly for local operations.

    Args:
        prompt: User prompt
        system: System prompt

    Returns:
        OpenAI's response text
    """
    try:
        import openai

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return "Error: OPENAI_API_KEY not configured"

        client = openai.OpenAI(api_key=api_key)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=4096
        )

        return response.choices[0].message.content

    except ImportError:
        return "Error: openai SDK not installed"
    except Exception as e:
        logger.error(f"OpenAI API call failed: {e}")
        return f"Error: {str(e)}"


# =============================================================================
# MCP Tool Implementations
# =============================================================================

async def dual_ai_task(
    task: str,
    project_path: str = ".",
    mode: str = "sequential",
    agents: Optional[List[str]] = None,
    max_iterations: int = 10,
) -> Dict[str, Any]:
    """
    Execute a Dual-AI collaborative task.

    Workflow:
    1. GPT-4 plans task decomposition
    2. Claude executes specific steps
    3. GPT-4 verifies results
    4. If issues found, iterate

    Args:
        task: Task description
        project_path: Project directory path
        mode: Collaboration mode (sequential, parallel, consensus)
        agents: List of agent IDs to use
        max_iterations: Maximum iteration count

    Returns:
        {
            "ok": bool,
            "session_id": str,
            "iterations": int,
            "plan": {...},
            "results": [...],
            "final_output": str,
        }
    """
    if agents is None:
        agents = ["planner", "executor", "reviewer"]

    # Try VPS first
    try:
        result = await call_vps("coordinate", {
            "task": task,
            "project_path": project_path,
            "mode": mode,
            "agents": agents,
            "max_iterations": max_iterations,
        })

        if result.get("ok") or result.get("session_id"):
            return {
                "ok": True,
                "session_id": result.get("session_id", "local-session"),
                "iterations": result.get("iteration", 1),
                "action": result.get("action", "complete"),
                "message": result.get("message", "Task processed"),
                "results": result.get("todo_list", []),
                "files": result.get("files", []),  # Include generated files
                "edits": result.get("edits", []),  # Include edits
                "metadata": result.get("metadata", {}),
            }
    except Exception as e:
        logger.warning(f"VPS call failed, falling back to local: {e}")

    # Fallback to local processing - Use GPT-4 for BOTH planning and execution
    # This allows the system to work without ANTHROPIC_API_KEY

    # Combined prompt: Plan AND execute with GPT-4
    combined_prompt = f"""Task: {task}

Project: {project_path}
Mode: {mode}

You are a full-stack AI that can both plan and execute tasks.

Please:
1. Analyze the task
2. Create a brief plan
3. EXECUTE the task - write actual code, create actual files, provide actual solutions

If the task asks you to create code or a website:
- Write the COMPLETE code
- Include ALL necessary HTML, CSS, JavaScript
- Make it production-ready

Return your response as JSON:
{{
    "plan": {{
        "steps": ["step1", "step2", ...],
        "approach": "brief description"
    }},
    "execution": {{
        "files": [
            {{
                "path": "filename.html",
                "content": "complete file content here"
            }}
        ],
        "explanation": "what was created and how to use it"
    }},
    "status": "complete|partial|failed"
}}

IMPORTANT: Actually write the code, don't just describe what to do."""

    combined_response = await call_openai_local(
        combined_prompt,
        system="You are a full-stack developer AI. You plan AND execute tasks. Write actual, complete, working code."
    )

    # Parse response
    result_data = {}
    try:
        json_match = re.search(r'\{[\s\S]*\}', combined_response)
        if json_match:
            result_data = json.loads(json_match.group())
    except json.JSONDecodeError:
        result_data = {
            "plan": {"steps": [task], "approach": "direct execution"},
            "execution": {"explanation": combined_response},
            "status": "partial"
        }

    return {
        "ok": True,
        "session_id": f"local-{hash(task) % 10000:04d}",
        "iterations": 1,
        "plan": result_data.get("plan", {}),
        "results": [{"step": "execution", "output": result_data.get("execution", {})}],
        "final_output": result_data.get("execution", {}).get("explanation", combined_response),
        "files": result_data.get("execution", {}).get("files", []),
        "status": result_data.get("status", "complete"),
    }


async def dual_ai_review(
    file_path: str,
    review_type: str = "all",
    models: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Multi-model code review.

    Uses Claude and GPT-4 simultaneously to review code, then merges results.

    Args:
        file_path: Path to file to review
        review_type: Type of review (security, performance, style, all)
        models: Models to use (default: ["claude", "gpt4"])

    Returns:
        {
            "file": str,
            "reviews": {
                "claude": {"issues": [...], "suggestions": [...]},
                "gpt4": {"issues": [...], "suggestions": [...]},
            },
            "merged": {
                "critical": [...],
                "high": [...],
                "medium": [...],
                "low": [...],
            },
            "consensus_score": float,
        }
    """
    if models is None:
        models = ["claude", "gpt4"]

    # Read file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
    except FileNotFoundError:
        return {"ok": False, "error": f"File not found: {file_path}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Try VPS first
    try:
        result = await call_vps("review", {
            "code": code,
            "file_path": file_path,
            "review_type": review_type,
            "models": models,
        })
        if result.get("ok"):
            return result
    except Exception as e:
        logger.warning(f"VPS review failed, falling back to local: {e}")

    # Local review with both models
    review_prompt = f"""Review this code for {review_type} issues:

File: {file_path}
```
{code[:4000]}  # Limit to 4000 chars
```

Return JSON:
{{
    "issues": [
        {{"severity": "critical|high|medium|low", "line": int, "description": "...", "suggestion": "..."}}
    ],
    "suggestions": ["..."],
    "overall_quality": "good|fair|poor"
}}"""

    reviews = {}

    # Run reviews in parallel
    tasks = []
    if "claude" in models:
        tasks.append(("claude", call_claude_local(
            review_prompt,
            system="You are a code review expert. Identify issues and provide actionable suggestions."
        )))
    if "gpt4" in models:
        tasks.append(("gpt4", call_openai_local(
            review_prompt,
            system="You are a code review expert. Identify issues and provide actionable suggestions."
        )))

    for model_name, task_coro in tasks:
        try:
            response = await task_coro
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                reviews[model_name] = json.loads(json_match.group())
            else:
                reviews[model_name] = {"issues": [], "suggestions": [response[:500]]}
        except Exception as e:
            reviews[model_name] = {"issues": [], "suggestions": [], "error": str(e)}

    # Merge results
    merged = {"critical": [], "high": [], "medium": [], "low": []}
    all_issues = []

    for model, review in reviews.items():
        for issue in review.get("issues", []):
            issue["source"] = model
            all_issues.append(issue)
            severity = issue.get("severity", "low")
            if severity in merged:
                merged[severity].append(issue)

    # Calculate consensus score
    if len(all_issues) > 0:
        # Count issues found by multiple models
        issue_descs = {}
        for issue in all_issues:
            desc = issue.get("description", "")[:50]
            if desc not in issue_descs:
                issue_descs[desc] = []
            issue_descs[desc].append(issue.get("source"))

        consensus_count = sum(1 for sources in issue_descs.values() if len(sources) > 1)
        consensus_score = consensus_count / len(issue_descs) if issue_descs else 0.0
    else:
        consensus_score = 1.0  # No issues = full consensus

    return {
        "ok": True,
        "file": file_path,
        "reviews": reviews,
        "merged": merged,
        "consensus_score": round(consensus_score, 2),
        "total_issues": len(all_issues),
    }


async def dual_ai_consensus(
    question: str,
    options: List[str],
    context: str = "",
    mode: str = "majority",
    voters: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Multi-AI consensus voting.

    Used for important decisions:
    - Choosing technical approach
    - Deciding whether to execute risky operations
    - Evaluating risk levels

    Args:
        question: Question to vote on
        options: List of options to choose from
        context: Additional context for decision
        mode: Voting mode (majority, unanimous, weighted)
        voters: List of voter IDs (default: ["claude", "gpt4"])

    Returns:
        {
            "question": str,
            "options": [...],
            "votes": {
                "claude": {"choice": "A", "confidence": 0.9, "reasoning": "..."},
                "gpt4": {"choice": "A", "confidence": 0.8, "reasoning": "..."},
            },
            "result": {
                "winner": "A",
                "vote_count": {"A": 2, "B": 0},
                "consensus_reached": True,
                "combined_reasoning": "...",
            },
        }
    """
    if voters is None:
        voters = ["claude", "gpt4"]

    # Try VPS first
    try:
        result = await call_vps("consensus", {
            "question": question,
            "options": options,
            "context": context,
            "mode": mode,
            "voters": voters,
        })
        if result.get("ok"):
            return result
    except Exception as e:
        logger.warning(f"VPS consensus failed, falling back to local: {e}")

    # Local consensus voting
    vote_prompt = f"""You are participating in a multi-AI voting system.

Question: {question}

Options:
{chr(10).join(f'{i+1}. {opt}' for i, opt in enumerate(options))}

Context: {context if context else 'None provided'}

Vote by returning JSON:
{{
    "choice": "option text or number",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation"
}}"""

    votes = {}

    # Collect votes
    for voter in voters:
        try:
            if voter == "claude":
                response = await call_claude_local(
                    vote_prompt,
                    system="You are a voting participant. Make a clear choice and explain your reasoning."
                )
            else:
                response = await call_openai_local(
                    vote_prompt,
                    system="You are a voting participant. Make a clear choice and explain your reasoning."
                )

            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                vote_data = json.loads(json_match.group())
                # Normalize choice to option index
                choice = vote_data.get("choice", options[0])
                if isinstance(choice, int) and 1 <= choice <= len(options):
                    choice = options[choice - 1]
                elif choice not in options:
                    # Try to match partial
                    for opt in options:
                        if str(choice).lower() in opt.lower():
                            choice = opt
                            break
                    else:
                        choice = options[0]

                votes[voter] = {
                    "choice": choice,
                    "confidence": float(vote_data.get("confidence", 0.7)),
                    "reasoning": vote_data.get("reasoning", "No reasoning provided"),
                }
            else:
                votes[voter] = {
                    "choice": options[0],
                    "confidence": 0.5,
                    "reasoning": response[:200],
                }
        except Exception as e:
            votes[voter] = {
                "choice": options[0],
                "confidence": 0.0,
                "reasoning": f"Error: {str(e)}",
            }

    # Tally votes
    vote_count = {opt: 0 for opt in options}
    for vote in votes.values():
        choice = vote.get("choice")
        if choice in vote_count:
            if mode == "weighted":
                vote_count[choice] += vote.get("confidence", 1.0)
            else:
                vote_count[choice] += 1

    # Determine winner
    winner = max(vote_count.items(), key=lambda x: x[1])[0]
    total_votes = sum(vote_count.values())

    # Check consensus based on mode
    if mode == "unanimous":
        consensus_reached = all(v["choice"] == winner for v in votes.values())
    elif mode == "majority":
        consensus_reached = vote_count[winner] > total_votes / 2
    else:  # weighted
        consensus_reached = vote_count[winner] > total_votes * 0.5

    # Combine reasoning
    reasonings = [f"{voter}: {v['reasoning']}" for voter, v in votes.items()]

    return {
        "ok": True,
        "question": question,
        "options": options,
        "votes": votes,
        "result": {
            "winner": winner,
            "vote_count": vote_count,
            "consensus_reached": consensus_reached,
            "combined_reasoning": "\n".join(reasonings),
        },
    }


async def dual_ai_security(
    target: str,
    scan_type: str = "full",
) -> Dict[str, Any]:
    """
    Professional security review agent.

    Checks for:
    - SQL Injection
    - XSS
    - CSRF
    - Sensitive data leaks
    - Auth/authz issues
    - Dependency vulnerabilities

    Args:
        target: File path or code snippet
        scan_type: Scan depth (quick, full, deep)

    Returns:
        {
            "target": str,
            "scan_type": str,
            "vulnerabilities": [
                {
                    "severity": "critical|high|medium|low",
                    "type": "sql_injection",
                    "location": {"file": "...", "line": 42},
                    "description": "...",
                    "fix_suggestion": "...",
                    "cwe_id": "CWE-89",
                },
            ],
            "summary": {
                "critical": 0,
                "high": 1,
                "medium": 3,
                "low": 5,
            },
            "recommendation": "...",
        }
    """
    # Determine if target is a file path or code snippet
    is_file = os.path.exists(target)

    if is_file:
        try:
            with open(target, 'r', encoding='utf-8') as f:
                code = f.read()
            file_path = target
        except Exception as e:
            return {"ok": False, "error": str(e)}
    else:
        code = target
        file_path = "<snippet>"

    # Try VPS first
    try:
        result = await call_vps("security_scan", {
            "code": code,
            "file_path": file_path,
            "scan_type": scan_type,
        })
        if result.get("ok"):
            return result
    except Exception as e:
        logger.warning(f"VPS security scan failed, falling back to local: {e}")

    # Local security scan with Claude (best for code analysis)
    security_prompt = f"""Perform a {scan_type} security audit on this code:

File: {file_path}
```
{code[:6000]}
```

Check for:
1. SQL Injection (CWE-89)
2. Cross-Site Scripting XSS (CWE-79)
3. CSRF vulnerabilities (CWE-352)
4. Sensitive data exposure (CWE-200)
5. Authentication issues (CWE-287)
6. Authorization issues (CWE-862)
7. Command injection (CWE-78)
8. Path traversal (CWE-22)
9. Insecure dependencies
10. Hardcoded secrets

Return JSON:
{{
    "vulnerabilities": [
        {{
            "severity": "critical|high|medium|low",
            "type": "vulnerability_type",
            "location": {{"file": "...", "line": 42}},
            "description": "detailed description",
            "fix_suggestion": "how to fix",
            "cwe_id": "CWE-XXX"
        }}
    ],
    "recommendation": "overall security recommendation"
}}"""

    response = await call_claude_local(
        security_prompt,
        system="You are a security expert. Identify vulnerabilities and provide actionable fixes."
    )

    # Parse response
    try:
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = {"vulnerabilities": [], "recommendation": response[:500]}
    except json.JSONDecodeError:
        result = {"vulnerabilities": [], "recommendation": response[:500]}

    # Build summary
    vulnerabilities = result.get("vulnerabilities", [])
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for vuln in vulnerabilities:
        severity = vuln.get("severity", "low")
        if severity in summary:
            summary[severity] += 1

    return {
        "ok": True,
        "target": file_path,
        "scan_type": scan_type,
        "vulnerabilities": vulnerabilities,
        "summary": summary,
        "total_vulnerabilities": len(vulnerabilities),
        "recommendation": result.get("recommendation", "No specific recommendations."),
    }


async def dual_ai_test_gen(
    target: str,
    test_type: str = "unit",
    framework: str = "auto",
) -> Dict[str, Any]:
    """
    Automatic test case generation.

    Process:
    1. Analyze target code
    2. Identify boundary conditions and important paths
    3. Generate test code
    4. Verify tests are executable

    Args:
        target: File path or function name
        test_type: Test type (unit, integration, e2e)
        framework: Test framework (pytest, jest, vitest, auto)

    Returns:
        {
            "target": str,
            "test_type": str,
            "framework": str,
            "tests": [
                {
                    "name": "test_function_normal_case",
                    "description": "Test normal case",
                    "code": "def test_...",
                    "covers": ["line 10-15", "branch A"],
                },
            ],
            "coverage_estimate": 85.5,
            "output_file": "test_xxx.py",
        }
    """
    # Check if target is a file
    is_file = os.path.exists(target)

    if is_file:
        try:
            with open(target, 'r', encoding='utf-8') as f:
                code = f.read()
            file_path = target
        except Exception as e:
            return {"ok": False, "error": str(e)}
    else:
        # Assume it's a code snippet or function name
        code = target
        file_path = "<snippet>"

    # Auto-detect framework based on file extension
    if framework == "auto":
        if file_path.endswith(".py"):
            framework = "pytest"
        elif file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
            framework = "jest"
        elif file_path.endswith(".vue"):
            framework = "vitest"
        else:
            framework = "pytest"  # Default

    # Try VPS first
    try:
        result = await call_vps("test_gen", {
            "code": code,
            "file_path": file_path,
            "test_type": test_type,
            "framework": framework,
        })
        if result.get("ok"):
            return result
    except Exception as e:
        logger.warning(f"VPS test gen failed, falling back to local: {e}")

    # Local test generation with Claude
    test_prompt = f"""Generate {test_type} tests for this code using {framework}:

File: {file_path}
```
{code[:5000]}
```

Generate comprehensive tests covering:
1. Normal/happy path cases
2. Edge cases
3. Error handling
4. Boundary conditions

Return JSON:
{{
    "tests": [
        {{
            "name": "test_name",
            "description": "what this test covers",
            "code": "complete test code",
            "covers": ["description of what code paths are covered"]
        }}
    ],
    "coverage_estimate": 0.0-100.0,
    "notes": "any additional notes"
}}"""

    response = await call_claude_local(
        test_prompt,
        system=f"You are a test engineer expert in {framework}. Generate comprehensive, runnable tests."
    )

    # Parse response
    try:
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            result = json.loads(json_match.group())
        else:
            # Try to extract code blocks as tests
            code_blocks = re.findall(r'```(?:\w+)?\n([\s\S]*?)```', response)
            result = {
                "tests": [
                    {
                        "name": f"test_{i+1}",
                        "description": "Generated test",
                        "code": code,
                        "covers": [],
                    }
                    for i, code in enumerate(code_blocks)
                ],
                "coverage_estimate": 50.0,
            }
    except json.JSONDecodeError:
        result = {"tests": [], "coverage_estimate": 0.0}

    # Determine output file name
    if is_file:
        base_name = Path(file_path).stem
        if framework == "pytest":
            output_file = f"test_{base_name}.py"
        else:
            output_file = f"{base_name}.test.{Path(file_path).suffix.lstrip('.')}"
    else:
        output_file = f"test_generated.{'.py' if framework == 'pytest' else '.js'}"

    return {
        "ok": True,
        "target": file_path,
        "test_type": test_type,
        "framework": framework,
        "tests": result.get("tests", []),
        "coverage_estimate": result.get("coverage_estimate", 0.0),
        "output_file": output_file,
        "notes": result.get("notes", ""),
    }


def dual_ai_agents() -> Dict[str, Any]:
    """
    List all available Dual-AI agents.

    Returns:
        {
            "agents": [
                {
                    "id": "planner",
                    "name": "Task Planner",
                    "model": "gpt-4o",
                    "capabilities": ["planning", "decomposition"],
                    "status": "available",
                },
                ...
            ],
            "models": {
                "openai": ["gpt-4o", "gpt-4o-mini"],
                "anthropic": ["claude-opus-4.5", "claude-sonnet-4"],
            },
        }
    """
    agents_list = [
        {
            "id": agent.id,
            "name": agent.name,
            "model": agent.model,
            "capabilities": agent.capabilities,
            "status": agent.status,
        }
        for agent in AVAILABLE_AGENTS
    ]

    return {
        "ok": True,
        "agents": agents_list,
        "total_agents": len(agents_list),
        "models": {
            "openai": ["gpt-4o", "gpt-4o-mini"],
            "anthropic": ["claude-opus-4.5", "claude-sonnet-4"],
        },
    }
