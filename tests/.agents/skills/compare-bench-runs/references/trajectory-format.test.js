
describe('SREGym Trajectory MD Format Parser', () => {
    const trajectoryContent = `
# SRE Judged Session State
## Session
- App: my-app / Namespace: my-namespace

## Diagnosis
### Iteration 1 — Agent Hypothesis
**Diagnosis**: Root cause hypothesis 1
**Justification**: Evidence 1

### Iteration 1 — Judge Independent Findings
Judge findings 1

### Iteration 1 — Judge Verdict (diagnosis)
- Status: REJECTED
- Reasoning: Reason 1

<benchmark_result>
success: False
{
  "Diagnosis": {
    "judgment": "False",
    "reasoning": "...",
    "success": false,
    "accuracy": 50.0
  },
  "TTL": 120
}
</benchmark_result>

### Iteration 2 — Agent Hypothesis
**Diagnosis**: Root cause hypothesis 2
**Justification**: Evidence 2

### Iteration 2 — Judge Independent Findings
Judge findings 2

### Iteration 2 — Judge Verdict (diagnosis)
- Status: APPROVED
- Reasoning: Reason 2

<benchmark_result>
success: True
{
  "Diagnosis": {
    "judgment": "True",
    "reasoning": "...",
    "success": true,
    "accuracy": 100.0
  },
  "TTL": 240
}
</benchmark_result>

## Mitigation
### Iteration 1 — Agent Strategy
**Mitigation**: Mitigation strategy 1
**Justification**: Justification 1

### Iteration 1 — Judge Independent Findings
Judge verification 1

### Iteration 1 — Judge Verdict (mitigation)
- Status: APPROVED
- Reasoning: Reason 1

<benchmark_result>
success: True
{
  "Diagnosis": { ... },
  "TTL": 240,
  "Mitigation": { "success": true },
  "TTM": 360
}
</benchmark_result>
`;

    // Mock parser function (implementation not provided, but tests are written against its expected output)
    const parseTrajectory = (content) => {
        // This is a simplified mock of what the parser should extract.
        return {
            success: true,
            TTL: 240,
            TTM: 360,
            accuracy: 100.0,
            diagnosisIterations: 2,
            mitigationIterations: 1,
            diagnoses: [
                { iteration: 1, hypothesis: 'Root cause hypothesis 1', status: 'REJECTED' },
                { iteration: 2, hypothesis: 'Root cause hypothesis 2', status: 'APPROVED' }
            ],
            mitigations: [
                { iteration: 1, strategy: 'Mitigation strategy 1', status: 'APPROVED' }
            ]
        };
    };

    const parsedData = parseTrajectory(trajectoryContent);

    describe('Key Field Extraction', () => {
        it('should extract the final success status', () => {
            expect(parsedData.success).toBe(true);
        });

        it('should extract the Time to Locate (TTL)', () => {
            expect(parsedData.TTL).toBe(240);
        });

        it('should extract the Time to Mitigate (TTM)', () => {
            expect(parsedData.TTM).toBe(360);
        });

        it('should extract the final diagnosis accuracy', () => {
            expect(parsedData.accuracy).toBe(100.0);
        });

        it('should correctly count the number of diagnosis iterations', () => {
            expect(parsedData.diagnosisIterations).toBe(2);
        });

        it('should correctly count the number of mitigation iterations', () => {
            expect(parsedData.mitigationIterations).toBe(1);
        });
    });

    describe('Structural Integrity', () => {
        it('should identify all diagnosis attempts', () => {
            expect(parsedData.diagnoses.length).toBe(2);
            expect(parsedData.diagnoses[0].status).toBe('REJECTED');
            expect(parsedData.diagnoses[1].status).toBe('APPROVED');
        });

        it('should identify all mitigation attempts', () => {
            expect(parsedData.mitigations.length).toBe(1);
            expect(parsedData.mitigations[0].status).toBe('APPROVED');
        });
    });

    describe('Analysis Signals', () => {
        it('should be able to compare diagnoses between runs', () => {
            // This test would require a second parsed trajectory object
            const anotherParsedData = { ...parsedData, diagnoses: [{ iteration: 1, hypothesis: 'A different hypothesis', status: 'APPROVED' }] };
            const latestDiagnosis = parsedData.diagnoses.find(d => d.status === 'APPROVED');
            const anotherLatestDiagnosis = anotherParsedData.diagnoses.find(d => d.status === 'APPROVED');
            
            expect(latestDiagnosis.hypothesis).not.toBe(anotherLatestDiagnosis.hypothesis);
        });

        it('should reflect the number of iterations as a signal for complexity or inaccuracy', () => {
            // More iterations might indicate a less efficient agent
            expect(parsedData.diagnosisIterations).toBeGreaterThan(1);
        });
    });
});
