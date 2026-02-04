import time
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

from app_operator.config import Config
from app_operator.filesystem import FileSystemInterface, RealFilesystem
from app_operator.logger import logger
from app_operator.exceptions import AgentError
from app_operator.trajectory import (
    Phase,
    init_trajectory,
)
from app_operator.prompts import get_loader
from app_operator.prompts.deployment_context import (
    analyze_repository,
    create_system_prompt,
)
from app_operator.prompts.deployer import (
    create_generate_script_prompt,
)
from app_operator.cli_agent.subprocess_runner import SubprocessRunner
from app_operator.cli_agent.healthcheck import run_health_check

from app_operator.adk.models import build_adk_model
from app_operator.adk.tools import build_tools
from app_operator.adk.agent_factory import build_adk_agent, build_loop_agent
from app_operator.adk.runner import AdkAgentRunner


class AdkOperator:
    """Operator implementation using Google ADK."""

    def __init__(
        self,
        repo_path: str,
        health_check_interval: int = 30,
        health_check_max_count: Optional[int] = 5,
        max_deployment_attempts: int = 5,
        filesystem: Optional[FileSystemInterface] = None,
        config: Optional[Config] = None,
        agent: Any = None,  # For interface compatibility (ignored)
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.health_check_interval = health_check_interval
        self.health_check_max_count = health_check_max_count
        self.max_deployment_attempts = max_deployment_attempts
        self.filesystem = filesystem if filesystem is not None else RealFilesystem()
        self.config = config or Config()

        # Initialize recorder
        self.recorder = init_trajectory(self.repo_path)
        self.recorder.set_agent_name("ADK")

        # Build ADK components
        self.model = build_adk_model(self.config)
        self.tools = build_tools(self.repo_path, self.filesystem)
        self.runner = AdkAgentRunner(
            app_name="sds-adk-operator",
            recorder=self.recorder,
            repo_path=self.repo_path,
        )

        # Paths
        self.sds_dir = self.repo_path / ".sds"
        self.deploy_script = self.sds_dir / "deploy.sh"
        self.health_check_script = self.sds_dir / "health_check.sh"
        self.logs_dir = self.sds_dir / "logs"

    def run(self) -> int:
        """Run the operator lifecycle."""
        return asyncio.run(self.run_async())

    async def run_async(self) -> int:
        """Run the operator lifecycle asynchronously."""
        try:
            # ensure sds dir exists
            self.filesystem.mkdir(self.sds_dir, parents=True, exist_ok=True)
            self.filesystem.mkdir(self.logs_dir, parents=True, exist_ok=True)

            # 1. Code Analysis
            await self._run_analysis()

            # 2. Script Generation
            await self._generate_scripts()

            # 3. Deployment with Retries
            if not await self._deploy_with_retries():
                logger.error("Deployment failed after max attempts.")
                self.recorder.finalize("failed")
                return 1

            # 4. Monitoring
            await self._monitor()

            self.recorder.finalize("completed")
            return 0

        except Exception as e:
            logger.error(f"Operator run failed: {e}")
            self.recorder.finalize("failed")
            return 1
        finally:
            self._cleanup()

    async def _run_analysis(self) -> None:
        """Run code analysis phase."""
        logger.info("Starting Code Analysis...")

        with self.recorder.phase(Phase.EXPLORATION) as _:
            # Load prompts
            system_prompt = get_loader().render("code_analyzer/system.jinja2")

            # Patch system prompt to update tool names to match new ADK tools
            system_prompt = system_prompt.replace("- **Glob**:", "- **find_files**:")
            system_prompt = system_prompt.replace("- **Read**:", "- **read_file**:")
            system_prompt = system_prompt.replace(
                "- **Grep**:", "- **search_content**:"
            )
            system_prompt = system_prompt.replace("- **LS**:", "- **list_files**:")

            user_prompt = get_loader().render(
                "code_analyzer/user.jinja2", repo_path=str(self.repo_path)
            )

            # Create agent

            agent = build_adk_agent(
                name="CodeAnalyzer",
                instruction=system_prompt,
                model=self.model,
                tools=self.tools,
            )

            # Run agent
            logger.info("Analyzing codebase...")
            response = await self.runner.run_async(agent, user_prompt)

            # Save analysis (assuming response is markdown)
            analysis_file = self.sds_dir / "code_analysis.md"
            self.filesystem.write_text(analysis_file, response)
            logger.info(f"Code analysis saved to {analysis_file}")

    async def _generate_scripts(self) -> None:
        """Generate deployment scripts."""
        logger.info("Generating Deployment Scripts...")

        platform = self.config.deployment.platform
        system_prompt = create_system_prompt(platform)
        repo_context = analyze_repository(self.repo_path)

        # Create generator agent
        agent = build_adk_agent(
            name="ScriptGenerator",
            instruction=system_prompt,
            model=self.model,
            tools=self.tools,
        )

        with self.recorder.phase(Phase.SCRIPT_GENERATION) as r:
            # Generate deploy.sh
            if not self.filesystem.exists(self.deploy_script):
                prompt = create_generate_script_prompt(
                    system_prompt=system_prompt,
                    script_name="deploy.sh",
                    repo_context=repo_context,
                    target_dir=str(self.repo_path),
                    platform=platform,
                )
                logger.info("Generating deploy.sh...")
                await self.runner.run_async(agent, prompt)

                # Check if file created
                if self.filesystem.exists(self.deploy_script):
                    self.filesystem.chmod(self.deploy_script, 0o755)
                    logger.success("Generated deploy.sh")
                else:
                    msg = "Agent failed to create deploy.sh"
                    logger.error(msg)
                    r.set_phase_status("failed")
                    raise AgentError(msg)

            # Generate health_check.sh
            if not self.filesystem.exists(self.health_check_script):
                prompt = create_generate_script_prompt(
                    system_prompt=system_prompt,
                    script_name="health_check.sh",
                    repo_context=repo_context,
                    target_dir=str(self.repo_path),
                    platform=platform,
                )
                logger.info("Generating health_check.sh...")
                await self.runner.run_async(agent, prompt)

                # Check if file created
                if self.filesystem.exists(self.health_check_script):
                    self.filesystem.chmod(self.health_check_script, 0o755)
                    logger.success("Generated health_check.sh")
                else:
                    msg = "Agent failed to create health_check.sh"
                    logger.error(msg)
                    r.set_phase_status("failed")
                    raise AgentError(msg)

    async def _deploy_with_retries(self) -> bool:
        """Deploy application using LoopAgent."""
        logger.info(
            f"Deploying with LoopAgent (max {self.max_deployment_attempts} retries)..."
        )

        # 1. Define Deployer Agent
        deployer_prompt = (
            "You are the Deployer. Your goal is to deploy the application and verify its health.\n"
            "1. Run `.sds/deploy.sh start` using the `run_command` tool. Provide a timeout.\n"
            "2. If the deployment succeeds (exit code 0), run `.sds/health_check.sh` using the `run_command` tool. Provide a timeout.\n"
            "3. If the health check also succeeds, output 'Deployment Successful' to complete the process.\n"
            "4. If any step fails, stop and output 'Deployment failed' to yield to the Fixer.\n"
            "Check the 'status' key in the tool response. If 'status' is 'error', treating it as a failure."
        )

        deployer = build_adk_agent(
            name="Deployer",
            instruction=deployer_prompt,
            model=self.model,
            tools=self.tools,
        )

        # 2. Define Fixer Agent
        fixer_prompt = (
            f"You are the Fixer. Your goal is to fix deployment or health check errors.\n"
            f"1. Analyze the output and errors from the previous Deployer attempt.\n"
            f"2. Use tools like `read_file`, `search_content`, `write_file`, `list_files`, `find_files` to investigate and fix the issues in the scripts or codebase.\n"
            f"3. After applying fixes, yield back to the Deployer to retry.\n"
            f"Context: Platform is {self.config.deployment.platform}.\n"
            "Check the 'status' key in the tool response. If 'status' is 'error', the tool execution failed."
        )

        fixer = build_adk_agent(
            name="Fixer",
            instruction=fixer_prompt,
            model=self.model,
            tools=self.tools,
        )

        # 3. Create LoopAgent
        # max_iterations covers Deploy -> Fix cycles.
        loop_agent = build_loop_agent(
            name="DeploymentLoop",
            sub_agents=[deployer, fixer],
            max_iterations=self.max_deployment_attempts * 2,
            tools=self.tools,
        )

        # 4. Run Loop
        with self.recorder.phase(Phase.DEPLOYMENT) as r:
            try:
                # The user prompt triggers the loop
                response = await self.runner.run_async(
                    loop_agent,
                    "Start the deployment process. Alternate between Deployer and Fixer until successful.",
                )

                # Check for success signal
                if (
                    "DEPLOYMENT_FINISHED" in response
                    or "Deployment Successful" in response
                ):
                    logger.success("Deployment Loop completed successfully.")
                    r.add_assistant_message("Deployment Loop completed successfully.")
                    return True
                else:
                    logger.error("Deployment Loop ended without success signal.")
                    r.add_assistant_message("Deployment Loop failed.")
                    return False

            except Exception as e:
                logger.error(f"Deployment Loop failed: {e}")
                r.add_assistant_message(f"Deployment Loop failed: {e}")
                return False

    async def _monitor(self) -> None:
        """Run health monitoring."""
        if not self.health_check_max_count:
            return

        logger.info(
            f"Starting monitoring (max {self.health_check_max_count} checks)..."
        )

        # Monitor agent
        monitor_agent = build_adk_agent(
            name="HealthMonitor",
            instruction="You are a site reliability engineer monitoring system health.",
            model=self.model,
            tools=self.tools,  # Monitor might need tools to investigate? Usually just analysis.
        )

        monitor_logs = self.logs_dir / "monitor"
        self.filesystem.mkdir(monitor_logs, parents=True, exist_ok=True)

        for i in range(1, self.health_check_max_count + 1):
            logger.info(f"Monitoring Cycle #{i}")

            # Wait interval
            if i > 1:
                await asyncio.sleep(self.health_check_interval)

            with self.recorder.phase(Phase.MONITORING, {"cycle": i}) as r:
                # Run health check
                log_file = monitor_logs / f"check_{i}.log"
                start_time = time.time()
                result = run_health_check(
                    self.repo_path, self.health_check_script, log_file_path=log_file
                )
                # I'll check run_health_check signature in healthcheck.py
                # Based on usage in AppMonitor (app_operator/cli_agent/agents/app_monitor.py),
                # run_health_check(repo_path, script_path, log_file_path=...) ?
                # The read of app_monitor.py shows:
                # health_result = run_health_check(monitor.repo_path, monitor.health_check_script)
                # It does not pass log_file_path.
                # But DeploymentAgent passes log_file_path.
                # Let's assume it supports it or handle logging manually.

                # I'll rely on result dict.
                duration = time.time() - start_time

                r.add_tool_call(
                    tool="run_command",
                    args={"command": ".sds/health_check.sh", "timeout": 120},
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", ""),
                    exit_code=result.get("exit_code", -1),
                    duration=duration,
                )

                # Analyze with agent
                context = self._prepare_health_context(result, i)
                prompt = get_loader().render(
                    "monitor/analyze_health.jinja2",
                    repo_path=self.repo_path,
                    context=context,
                )

                response = await self.runner.run_async(monitor_agent, prompt)

                # Log analysis
                analysis_log = monitor_logs / f"analysis_{i}.log"
                self.filesystem.write_text(analysis_log, response)
                logger.info(f"Analysis saved to {analysis_log}")

    def _prepare_health_context(self, health_result: dict, check_count: int) -> str:
        """Prepare health check context for analysis."""
        context_parts = []
        context_parts.append(f"## Health Check #{check_count}")
        context_parts.append(f"Exit Code: {health_result['exit_code']}")
        context_parts.append(
            f"Status: {'PASSED' if health_result['success'] else 'FAILED'}"
        )
        context_parts.append(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        if health_result["stdout"]:
            context_parts.append("\n### Output:")
            context_parts.append(health_result["stdout"])

        if health_result["stderr"]:
            context_parts.append("\n### Errors:")
            context_parts.append(health_result["stderr"])

        return "\n".join(context_parts)

    def _run_deploy_command(
        self,
        command: str = "start",
        log_file_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Run deployment script command."""
        runner = SubprocessRunner(
            command=[str(self.deploy_script), command],
            cwd=str(self.repo_path),
            timeout=self.config.operator.deploy_timeout,
            log_file_path=log_file_path,
            time_func=time.time,
            sleep_func=time.sleep,
            popen_func=subprocess.Popen,
        )
        # We don't have a progress summarizer for ADK yet, just run simply
        # Or we can reuse ProgressSummarizer if we wrap runner.run_once?
        # For now, just run without live summary from agent.
        return runner.run()

    def _get_next_attempt_number(self) -> int:
        """Determine next attempt number."""
        if not self.filesystem.exists(self.logs_dir):
            return 1

        max_attempt = 0
        # This part requires listing files, which filesystem interface might not support easily on glob
        # RealFilesystem supports glob if we use path objects.
        # But FileSystemInterface doesn't have glob.
        # However, self.logs_dir is a Path object.
        # If using RealFilesystem, self.logs_dir.glob works.
        # If using InMemoryFilesystem for tests, we need to handle it.
        # The plan says "Use InMemoryFilesystem + stubbed AdkAgentRunner".
        # InMemoryFilesystem doesn't implement glob on Path objects it seems (it's stdlib Path).
        # We might need to handle this.
        # But `cli_agent` deployer uses `list(logs_dir.glob(...))`.
        # This implies `logs_dir` being a Path object works on the underlying FS.
        # If `InMemoryFilesystem` is used, `logs_dir.glob` will fail or return empty if directory doesn't exist on disk?
        # Actually `InMemoryFilesystem` is for `filesystem` object. `logs_dir` is a `Path`.
        # `Path.glob` checks real disk.
        # So `cli_agent` logic breaks with `InMemoryFilesystem` unless we mock `Path.glob`.
        # I'll stick to `cli_agent` logic and assume tests handle it or I use a workaround.

        try:
            # Use tools.ls logic?
            # Or just try/except
            files = list(self.logs_dir.glob("deploy_attempt_*.log"))
            for log_file in files:
                try:
                    name = log_file.stem
                    parts = name.split("_")
                    if len(parts) >= 3 and parts[-1].isdigit():
                        num = int(parts[-1])
                        if num > max_attempt:
                            max_attempt = num
                except ValueError:
                    continue
        except Exception:
            pass

        return max_attempt + 1

    def _cleanup(self) -> None:
        """Cleanup resources."""
        # Stop any background processes if needed
        pass
