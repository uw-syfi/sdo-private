package runtime

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"sdo.dev/controller/core"
	"sdo.dev/controller/sdk"
)

type RuntimeOptions struct {
	Args     []string
	Stdout   io.Writer
	Stderr   io.Writer
	Provider SnapshotProvider
}

type repeatedFlag []string

func (values *repeatedFlag) String() string { return fmt.Sprint([]string(*values)) }
func (values *repeatedFlag) Set(value string) error {
	*values = append(*values, value)
	return nil
}

func Run(detectors []sdk.Detector) {
	if err := RunWithOptions(context.Background(), detectors, RuntimeOptions{}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func RunWithOptions(ctx context.Context, detectors []sdk.Detector, options RuntimeOptions) error {
	args := options.Args
	if args == nil {
		args = os.Args[1:]
	}
	stdout := options.Stdout
	if stdout == nil {
		stdout = os.Stdout
	}
	stderr := options.Stderr
	if stderr == nil {
		stderr = os.Stderr
	}
	if len(args) > 0 && args[0] == "--run-once" {
		return core.RunWithOptions(ctx, detectors, core.RuntimeOptions{Args: args[1:], Stdout: stdout, Stderr: stderr})
	}
	if len(args) > 0 && args[0] == "--evaluate-once" {
		return evaluateOnce(ctx, detectors, args[1:], stdout, stderr, options.Provider)
	}

	flags := flag.NewFlagSet("sdo-controller", flag.ContinueOnError)
	flags.SetOutput(stderr)
	namespace := flags.String("namespace", "", "Kubernetes namespace to observe")
	appRoot := flags.String("app-root", "", "application repository root")
	application := flags.String("application", "", "application identity; defaults to repository directory name")
	sourceCommit := flags.String("source-commit", "", "source repository commit; defaults to git HEAD")
	deployedCommit := flags.String("deployed-commit", "", "deployed commit; defaults to source commit")
	dispatcherCommand := flags.String("dispatcher", "", "incident responder executable")
	dispatcherMode := flags.String("dispatcher-mode", "job", "dispatcher mode: job (production) or local (development)")
	responderImage := flags.String("responder-image", "", "responder container image for job mode")
	repositoryPVC := flags.String("repository-pvc", "", "application repository PVC for job mode")
	repositoryMountPath := flags.String("repository-mount-path", "/workspace", "shared repository PVC mount root for job mode")
	repositoryPVCSubPath := flags.String("repository-pvc-subpath", "", "optional shared repository PVC subPath for job mode")
	credentialsSecret := flags.String("responder-credentials-secret", "", "Codex credentials Secret for job mode")
	brokerCommand := flags.String("broker", "", "trusted incident broker executable")
	brokerWorktreeRoot := flags.String("broker-worktree-root", "", "shared root for isolated incident worktrees")
	var dispatcherArgs repeatedFlag
	flags.Var(&dispatcherArgs, "dispatcher-arg", "incident responder argument; may be repeated")
	var responderEnvironment repeatedFlag
	flags.Var(&responderEnvironment, "responder-env", "responder Job environment NAME=VALUE; may be repeated")
	var brokerArgs repeatedFlag
	flags.Var(&brokerArgs, "broker-arg", "incident broker argument; may be repeated")
	duration := flags.Duration("duration", 0, "bounded controller duration; zero runs until cancellation")
	responseTimeout := flags.Duration("response-timeout", 30*time.Minute, "incident responder timeout")
	leaseName := flags.String("lease-name", "sdo-controller", "leader-election Lease name")
	leaseDuration := flags.Duration("lease-duration", 60*time.Second, "leader-election Lease duration")
	identity := flags.String("identity", defaultIdentity(), "unique leader-election identity")
	exitAfterClosure := flags.Bool("exit-after-closure", false, "exit after one closure is durably acknowledged")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *namespace == "" || *appRoot == "" {
		return fmt.Errorf("namespace and app-root are required")
	}
	if *dispatcherCommand == "" || (*dispatcherMode == "job" && (*responderImage == "" || *repositoryPVC == "" || *credentialsSecret == "")) {
		return fmt.Errorf("dispatcher is required; responder-image, repository-pvc, and responder-credentials-secret are required in job mode")
	}
	if *dispatcherMode == "job" && (*brokerCommand == "" || *brokerWorktreeRoot == "") {
		return fmt.Errorf("broker and broker-worktree-root are required in production job mode")
	}
	resolvedRoot, err := filepath.Abs(*appRoot)
	if err != nil {
		return fmt.Errorf("resolve app root: %w", err)
	}
	if *application == "" {
		*application = filepath.Base(resolvedRoot)
	}
	resolvedSourceCommit, resolvedDeployedCommit, err := resolveRepositoryCommits(
		resolvedRoot,
		*sourceCommit,
		*deployedCommit,
	)
	if err != nil {
		return err
	}
	bootstrapProvider, err := core.NewKubernetesSnapshotProvider(*namespace)
	if err != nil {
		return fmt.Errorf("create Kubernetes snapshot provider: %w", err)
	}
	leaseClient, err := core.NewKubernetesClient()
	if err != nil {
		return fmt.Errorf("create dedicated leader-election client: %w", err)
	}
	elector := NewLeaseElector(leaseClient, *namespace, *leaseName, *identity, *leaseDuration)
	for {
		leader, acquireErr := elector.TryAcquireOrRenew(ctx)
		if acquireErr != nil {
			return fmt.Errorf("acquire controller leadership: %w", acquireErr)
		}
		if leader {
			break
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(*leaseDuration / 3):
		}
	}
	defer func() {
		releaseCtx, cancelRelease := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancelRelease()
		if err := elector.Release(releaseCtx); err != nil {
			fmt.Fprintln(stderr, err)
		}
	}()
	renewalCtx, cancelRenewal := context.WithCancel(ctx)
	defer cancelRenewal()
	renewalInterval := *leaseDuration / 3
	if renewalInterval <= 0 {
		renewalInterval = time.Second
	}
	if renewalInterval > 5*time.Second {
		renewalInterval = 5 * time.Second
	}
	renewalErrors := maintainLeadership(renewalCtx, elector, renewalInterval)
	kubernetesCache, err := NewKubernetesCache(KubernetesCacheConfig{
		Namespace: *namespace, Client: bootstrapProvider.Client, StateConfigMapName: "sdo-controller-state",
	}, detectors)
	if err != nil {
		return fmt.Errorf("create Kubernetes informer cache: %w", err)
	}
	argv := append([]string{*dispatcherCommand}, dispatcherArgs...)
	jobEnvironment, err := parseResponderEnvironment(responderEnvironment)
	if err != nil {
		return err
	}
	var dispatcher Dispatcher
	switch *dispatcherMode {
	case "local":
		dispatcher = SubprocessDispatcher{Argv: argv, Timeout: *responseTimeout}
	case "job":
		dispatcher = KubernetesJobDispatcher{
			Client: bootstrapProvider.Client, Namespace: *namespace, Image: *responderImage,
			Command: argv, ServiceAccount: "sdo-responder", RepositoryPVC: *repositoryPVC,
			RepositoryMountPath: *repositoryMountPath, RepositoryPVCSubPath: *repositoryPVCSubPath,
			CredentialsSecret: *credentialsSecret, PollInterval: time.Second,
			Environment: jobEnvironment,
		}
	default:
		return fmt.Errorf("unsupported dispatcher mode %q", *dispatcherMode)
	}
	start := time.Now().UTC()
	controller, err := NewController(ControllerConfig{
		Application: *application, Namespace: *namespace,
		SourceCommit: resolvedSourceCommit, DeployedCommit: resolvedDeployedCommit,
		ArchitectureSummaryPath: ".sdo/arch.md", HealthObjectivePath: ".sdo/goal.md",
		RepositoryWorktree: resolvedRoot, ResponseTimeout: *responseTimeout,
		FiringThreshold: 2, ClearThreshold: 2, BatchDebounce: 500 * time.Millisecond,
	}, detectors, kubernetesCache, dispatcher, start)
	if err != nil {
		return err
	}
	if *brokerCommand != "" {
		if *brokerWorktreeRoot == "" {
			return fmt.Errorf("broker-worktree-root is required when broker is configured")
		}
		brokerArgv := append([]string{*brokerCommand}, brokerArgs...)
		brokerArgv = append(
			brokerArgv,
			"--repository", resolvedRoot,
			"--worktree-root", *brokerWorktreeRoot,
		)
		if err := controller.SetIncidentBroker(SubprocessIncidentBroker{
			Argv: brokerArgv, Timeout: *responseTimeout,
		}); err != nil {
			return err
		}
	}
	controller.CanAct = elector.IsLeader
	controller.GuardAction = elector.GuardContext
	stateStore := NewConfigMapStateStore(bootstrapProvider.Client, *namespace, "sdo-controller-state")
	if err := controller.AttachStateStore(ctx, stateStore); err != nil {
		return fmt.Errorf("restore controller state: %w", err)
	}
	if *exitAfterClosure && controller.LastAcknowledgedIncidentID() != "" {
		return nil
	}
	iteration := 0
	encoder := json.NewEncoder(stdout)
	controller.OnError = func(err error) { fmt.Fprintln(stderr, err) }
	controller.OnEvaluation = func(findings []sdk.Finding) {
		if err := encoder.Encode(map[string]any{
			"controller_iteration": iteration,
			"returncode":           0,
			"findings":             findings,
		}); err != nil {
			fmt.Fprintln(stderr, err)
		}
		iteration++
	}

	runCtx := ctx
	var cancel context.CancelFunc
	if *duration > 0 {
		runCtx, cancel = context.WithTimeout(ctx, *duration)
		defer cancel()
	}
	kubernetesCache.Start(runCtx)
	if err := kubernetesCache.WaitForSync(runCtx); err != nil {
		return fmt.Errorf("sync Kubernetes informer cache: %w", err)
	}
	if err := controller.Step(runCtx, time.Now().UTC(), nil); err != nil {
		if runCtx.Err() != nil {
			return nil
		}
		return err
	}
	if err := controller.PersistState(ctx); err != nil {
		return fmt.Errorf("persist controller state: %w", err)
	}
	if err := executePendingEffects(runCtx, controller); err != nil {
		if runCtx.Err() != nil {
			return nil
		}
		return err
	}
	for {
		if *exitAfterClosure && controller.LastAcknowledgedIncidentID() != "" {
			return nil
		}
		delay := time.Until(controller.NextWake())
		if delay < 0 {
			delay = 0
		}
		timer := time.NewTimer(delay)
		select {
		case <-runCtx.Done():
			stopTimerForRuntimeEvent(runCtx, timer)
			return nil
		case event := <-kubernetesCache.Events():
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			if err := controller.Step(runCtx, time.Now().UTC(), &event); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
			if err := controller.PersistState(ctx); err != nil {
				return fmt.Errorf("persist controller state: %w", err)
			}
			if err := executePendingEffects(runCtx, controller); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
		case completion := <-controller.results:
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			controller.handleDispatchCompletion(completion, time.Now().UTC())
			if err := controller.PersistState(ctx); err != nil {
				return fmt.Errorf("persist controller state: %w", err)
			}
			if err := executePendingEffects(runCtx, controller); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
		case completion := <-controller.workspaceResults:
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			controller.handleWorkspaceCompletion(completion)
			if err := controller.PersistState(ctx); err != nil {
				return fmt.Errorf("persist controller state: %w", err)
			}
			if err := executePendingEffects(runCtx, controller); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
		case completion := <-controller.closureResults:
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			controller.handleClosureCompletion(completion)
			if err := controller.PersistState(ctx); err != nil {
				return fmt.Errorf("persist controller state: %w", err)
			}
			if err := executePendingEffects(runCtx, controller); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
		case completion := <-controller.acknowledgmentResults:
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			controller.handleAcknowledgmentCompletion(completion)
			if err := controller.PersistState(ctx); err != nil {
				return fmt.Errorf("persist controller state: %w", err)
			}
			if err := executePendingEffects(runCtx, controller); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
			if *exitAfterClosure && completion.err == nil {
				return nil
			}
		case renewErr := <-renewalErrors:
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			return renewErr
		case <-timer.C:
			if stopTimerForRuntimeEvent(runCtx, timer) {
				return nil
			}
			if err := controller.Step(runCtx, time.Now().UTC(), nil); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
			if err := controller.PersistState(ctx); err != nil {
				return fmt.Errorf("persist controller state: %w", err)
			}
			if err := executePendingEffects(runCtx, controller); err != nil {
				if runCtx.Err() != nil {
					return nil
				}
				return err
			}
		}
	}
}

func stopTimerForRuntimeEvent(ctx context.Context, timer *time.Timer) bool {
	if !timer.Stop() {
		select {
		case <-timer.C:
		default:
		}
	}
	return ctx.Err() != nil
}

func maintainLeadership(
	ctx context.Context,
	elector *LeaseElector,
	interval time.Duration,
) <-chan error {
	errors := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				attemptTimeout := interval / 2
				if attemptTimeout <= 0 {
					attemptTimeout = time.Second
				}
				attemptCtx, cancelAttempt := context.WithTimeout(ctx, attemptTimeout)
				leader, err := elector.TryAcquireOrRenew(attemptCtx)
				cancelAttempt()
				if err != nil {
					if elector.IsLeader() {
						continue
					}
					errors <- fmt.Errorf("renew controller leadership before Lease expiry: %w", err)
					return
				}
				if !leader {
					errors <- fmt.Errorf("controller leadership lost")
					return
				}
			}
		}
	}()
	return errors
}

var environmentNamePattern = regexp.MustCompile(`^[A-Z_][A-Z0-9_]*$`)

func parseResponderEnvironment(entries []string) (map[string]string, error) {
	reserved := map[string]bool{
		"SDO_REQUEST_CONFIGMAP": true,
		"SDO_RESULT_CONFIGMAP":  true,
		"SDO_NAMESPACE":         true,
	}
	result := make(map[string]string, len(entries))
	for _, entry := range entries {
		name, value, ok := strings.Cut(entry, "=")
		if !ok || !environmentNamePattern.MatchString(name) {
			return nil, fmt.Errorf("responder environment must use NAME=VALUE with a valid uppercase name: %q", entry)
		}
		if reserved[name] {
			return nil, fmt.Errorf("responder environment %q is managed by the controller", name)
		}
		result[name] = value
	}
	return result, nil
}

func resolveRepositoryCommits(repository string, sourceCommit string, deployedCommit string) (string, string, error) {
	resolvedSource := strings.TrimSpace(sourceCommit)
	if resolvedSource == "" {
		completed := exec.Command("git", "-C", repository, "rev-parse", "HEAD")
		output, err := completed.CombinedOutput()
		if err != nil {
			return "", "", fmt.Errorf("resolve source commit: %w: %s", err, strings.TrimSpace(string(output)))
		}
		resolvedSource = strings.TrimSpace(string(output))
	}
	resolvedDeployed := strings.TrimSpace(deployedCommit)
	if resolvedDeployed == "" {
		resolvedDeployed = resolvedSource
	}
	if resolvedSource == "" || resolvedDeployed == "" {
		return "", "", fmt.Errorf("source and deployed commits are required")
	}
	return resolvedSource, resolvedDeployed, nil
}

type evaluationDispatcher struct{}

func (evaluationDispatcher) Dispatch(context.Context, IncidentRequest) (IncidentResult, error) {
	return IncidentResult{}, fmt.Errorf("evaluate-once does not dispatch incidents")
}

func evaluateOnce(
	ctx context.Context,
	detectors []sdk.Detector,
	args []string,
	stdout io.Writer,
	stderr io.Writer,
	provider SnapshotProvider,
) error {
	flags := flag.NewFlagSet("sdo-controller-evaluate-once", flag.ContinueOnError)
	flags.SetOutput(stderr)
	namespace := flags.String("namespace", "", "Kubernetes namespace to observe")
	application := flags.String("application", "", "application identity")
	flags.String("app-root", "", "application repository root")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *namespace == "" {
		return fmt.Errorf("namespace is required")
	}
	if *application == "" {
		*application = *namespace
	}
	if provider == nil {
		kubernetesProvider, err := core.NewKubernetesSnapshotProvider(*namespace)
		if err != nil {
			return fmt.Errorf("create Kubernetes snapshot provider: %w", err)
		}
		provider = kubernetesProvider
	}
	start := time.Now().UTC()
	controller, err := NewController(ControllerConfig{
		Application: *application, Namespace: *namespace,
		SourceCommit: "evaluation", DeployedCommit: "evaluation",
		ArchitectureSummaryPath: ".sdo/arch.md", HealthObjectivePath: ".sdo/goal.md",
		RepositoryWorktree: ".", ResponseTimeout: time.Minute,
		FiringThreshold: 1, ClearThreshold: 1,
	}, detectors, provider, evaluationDispatcher{}, start)
	if err != nil {
		return err
	}
	findings := make([]sdk.Finding, 0)
	var detectorErrors []error
	controller.OnEvaluation = func(sample []sdk.Finding) { findings = append([]sdk.Finding{}, sample...) }
	controller.OnError = func(err error) { detectorErrors = append(detectorErrors, err) }
	if err := controller.Step(ctx, start, nil); err != nil {
		return err
	}
	if len(detectorErrors) > 0 {
		return fmt.Errorf("health detector evaluation failed: %w", detectorErrors[0])
	}
	status := DetectorEvaluationClear
	for _, finding := range findings {
		if finding.Status == sdk.FindingActive {
			status = DetectorEvaluationFiring
			break
		}
	}
	return json.NewEncoder(stdout).Encode(map[string]any{"status": status, "findings": findings})
}

func defaultIdentity() string {
	hostname, err := os.Hostname()
	if err != nil || hostname == "" {
		hostname = "sdo-controller"
	}
	return hostname + "-" + strconv.Itoa(os.Getpid())
}

func executePendingEffects(ctx context.Context, controller *Controller) error {
	if effect, ok := controller.PendingWorkspaceEffect(); ok {
		if err := controller.ExecuteWorkspaceEffect(ctx, effect); err != nil {
			return fmt.Errorf("execute persisted workspace effect: %w", err)
		}
		return nil
	}
	effect, ok := controller.PendingDispatchEffect()
	if ok {
		if err := controller.ExecuteDispatchEffect(ctx, effect); err != nil {
			return fmt.Errorf("execute persisted dispatch effect: %w", err)
		}
		return nil
	}
	if closure, ok := controller.PendingClosureEffect(); ok {
		if err := controller.ExecuteClosureEffect(ctx, closure); err != nil {
			return fmt.Errorf("execute persisted closure effect: %w", err)
		}
		return nil
	}
	if acknowledgment, ok := controller.PendingClosureAcknowledgmentEffect(); ok {
		if err := controller.ExecuteClosureAcknowledgmentEffect(ctx, acknowledgment); err != nil {
			return fmt.Errorf("execute persisted closure acknowledgment effect: %w", err)
		}
	}
	return nil
}
