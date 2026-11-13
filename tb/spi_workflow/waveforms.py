"""waveforms helpers for the SPI notebook workflow."""

# import only the dependencies used by this module.
from pathlib import Path


# count actual top-level SCK edges independently of the testbench summary.
def count_sck_edges(path, top="crypto_tb"):
    scope, symbol, value, rising = [], None, None, 0
    # stream VCD data instead of loading all internal-net activity at once.
    with Path(path).open() as stream:
        # identify the top-level signal, then count scalar changes.
        for line in stream:
            parts = line.split()
            # track scope to avoid confusing unrelated internal clocks.
            if line.startswith("$scope"):
                scope.append(parts[2])
            elif line.startswith("$upscope"):
                scope.pop()
            elif line.startswith("$var") and scope == [top] and parts[4] == "SCK":
                symbol = parts[3]
            elif symbol and line.strip() in ("0" + symbol, "1" + symbol, "x" + symbol, "z" + symbol):
                new = line[0]
                # count only real low-to-high transitions.
                if new == "1" and value == "0":
                    rising += 1
                value = new
    # missing clocks indicate an invalid or incomplete waveform.
    if symbol is None:
        raise ValueError(f"VCD does not contain {top}.SCK")
    return rising


# retain detailed diagnostics with a failing run result.
class SimulationError(RuntimeError):
    # attach structured results for the notebook's repair prompt.
    def __init__(self, message, result):
        super().__init__(message)
        self.result = result
