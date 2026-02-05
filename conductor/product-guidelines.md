# Product Guidelines

These guidelines define the operational principles, design philosophy, and standards for the SDS (Self-Defining Systems) project.

## Interaction and Tone
- **Professional and Precise:** All system outputs, including CLI messages, logs, and interactive prompts, must be formal, concise, and technically accurate. Avoid unnecessary jargon or conversational filler.
- **Clarity Over Brevity:** While conciseness is valued, providing sufficient technical context to understand system state and decisions is paramount.

## Operational Philosophy
- **Autonomy-Max:** The system is designed to prioritize autonomy and self-correction. It should proactively attempt to resolve issues and proceed with its tasks unless a critical, unrecoverable failure occurs or a high-risk architectural decision is required.
- **Resilience:** The system must be robust against transient failures and strive to maintain a functional state through autonomous intervention.

## Architectural and Orchestration Principles
- **Modularity:** Multi-agent orchestration patterns (e.g., `fan_out`, `summarize`) must be implemented as self-contained, reusable modules. They should be easily composable to form complex workflows.
- **Observability:** Every stage of a multi-agent workflow must be fully traceable. Detailed logs and trajectory recordings are required to allow for post-hoc analysis and debugging of agent decisions.

## Development and Coding Standards
- **Explicit Typing:** Strict adherence to Python type hints is mandatory for all function signatures, class attributes, and complex data structures. This ensures code clarity and facilitates static analysis.
- **Functional Core, Imperative Shell:** Strive to keep core logic as pure, side-effect-free functions that are easy to unit test. Manage I/O, state changes, and external tool calls at the boundaries of the system.
- **Comprehensive Documentation:** All public APIs, including classes and methods, must include clear docstrings (following Google or NumPy style) that describe their purpose, parameters, and return values.

## Feature Management (Stability and Experimentation)
- **High Rigor for All Features:** Experimental features (like `lego_agent`) must be developed with the same level of technical rigor as stable ones. This includes comprehensive testing and adherence to all coding standards.
- **Transparent Lifecycle:** Clearly label experimental or "beta" features to manage user expectations regarding stability. Actively solicit user feedback to drive the evolution of these features towards maturity.
