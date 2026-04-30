
import unittest
import dspy

# Define a simple signature for our DSPy program.
class BasicQA(dspy.Signature):
    """Answer questions with short factoid answers."""
    question = dspy.InputField()
    answer = dspy.OutputField()

# Define a simple DSPy program.
class SimpleModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.predictor = dspy.Predict(BasicQA)

    def forward(self, question):
        return self.predictor(question=question)

class TestDSPyOptimizationInput(unittest.TestCase):
    def test_examples_are_input_to_optimization(self):
        # 1. Define the training data (the "input" to the optimization).
        # These are the dspy.Example objects.
        trainset = [
            dspy.Example(question="What is the capital of France?", answer="Paris").with_inputs("question"),
            dspy.Example(question="Who wrote 'The Lord of the Rings'?", answer="J.R.R. Tolkien").with_inputs("question"),
        ]

        # 2. Define a mock teleprompter (the optimizer).
        # We'll check that the trainset is passed to its 'compile' method.
        class MockTeleprompter:
            def __init__(self):
                self.compile_was_called = False
                self.received_trainset = None

            def compile(self, student, *, trainset):
                self.compile_was_called = True
                self.received_trainset = trainset
                return student # Return the student module as a compiled program.

        # 3. Instantiate the module, optimizer, and run the optimization.
        student_program = SimpleModule()
        mock_optimizer = MockTeleprompter()

        # The key step: The 'trainset' is the input to the compile method.
        compiled_program = mock_optimizer.compile(student_program, trainset=trainset)


        # 4. Assert that the optimization process received the trainset.
        self.assertTrue(mock_optimizer.compile_was_called)
        self.assertEqual(mock_optimizer.received_trainset, trainset)
        self.assertIsInstance(compiled_program, SimpleModule)

if __name__ == '__main__':
    unittest.main()
