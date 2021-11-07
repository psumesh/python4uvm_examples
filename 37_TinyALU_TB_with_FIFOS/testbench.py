import cocotb
from pyuvm import *
import random
from pathlib import Path
import sys
# All testbenches use tinyalu_utils, so store it in a central
# place and add its path to the sys path so we can import it
sys.path.append(str(Path("..").resolve()))
from tinyalu_utils import TinyAluBfm, Ops, alu_prediction  # noqa: E402


class RandomTester(uvm_component):

    def build_phase(self):
        self.bpp = uvm_blocking_put_port("bpp", self)

    async def until_done(self):
        await self.bpp.put((0, 0, Ops.ADD))
        await self.bpp.put((0, 0, Ops.ADD))
        await self.bpp.put((0, 0, Ops.ADD))
        await self.bpp.put((0, 0, Ops.ADD))

    async def run_phase(self):
        self.raise_objection()
        ops = list(Ops)
        for op in ops:
            aa = random.randint(0, 255)
            bb = random.randint(0, 255)
            await self.bpp.put((aa, bb, op))
        # send two dummy operations to allow
        # last real operation to complete
        await self.until_done()
        self.drop_objection()


class Driver(uvm_driver):

    def build_phase(self):
        self.bfm = ConfigDB().get(self, "", "BFM")
        self.bgp = uvm_blocking_get_port("bgp", self)

    async def run_phase(self):
        await self.bfm.reset()
        await self.bfm.start_bfms()
        while True:
            aa, bb, op = await self.bgp.get()
            await self.bfm.send_op(aa, bb, op)


class MaxTester(RandomTester):

    async def run_phase(self):
        self.raise_objection()
        ops = list(Ops)
        for op in ops:
            aa = 0xFF
            bb = 0xFF
            await self.bpp.put((aa, bb, op))
        # send two dummy operations to allow
        # last real operation to complete
        await self.until_done()
        self.drop_objection()


class Monitor(uvm_monitor):
    def __init__(self, name, parent, method_name):
        super().__init__(name, parent)
        self.method_name = method_name

    def build_phase(self):
        self.ap = uvm_analysis_port("ap", self)
        self.bfm = ConfigDB().get(self, "", "BFM")

    async def run_phase(self):
        while True:
            get_method = getattr(self.bfm, self.method_name)
            datum = await get_method()
            self.ap.write(datum)


class Scoreboard(uvm_component):

    def build_phase(self):
        self.cmd_gp = uvm_nonblocking_get_port("cmd_gp", self)
        self.result_gp = uvm_nonblocking_get_port("result_gp", self)

    def check_phase(self):
        passed = True
        cvg = set()
        while True:
            success, cmd = self.cmd_gp.try_get()
            if not success:
                break
            (aa, bb, op) = cmd
            cvg.add(Ops(op))
            result_there, actual = self.result_gp.try_get()
            assert result_there, f"Missing result for command {cmd}"
            prediction = alu_prediction(aa, bb, Ops(op))
            if actual == prediction:
                self.logger.info(f"PASSED: {aa} {Ops(op).name} {bb} = {actual}")
            else:
                passed = False
                self.logger.error(
                    f"FAILED: {aa} {Ops(op).name} {bb} = {actual} - predicted {prediction}")

        if len(set(Ops) - cvg) > 0:
            self.logger.error(
                f"Functional coverage error. Missed: {set(Ops)-cvg}")
            passed = False
        else:
            self.logger.info("Covered all operations")
        assert passed


class RandomAluEnv(uvm_env):
    """Instantiate the BFM and scoreboard"""

    def build_phase(self):
        dut = ConfigDB().get(self, "", "DUT")
        bfm = TinyAluBfm(dut)
        ConfigDB().set(None, "*", "BFM", bfm)
        self.driver = Driver("driver", self)
        self.tester = RandomTester("tester", self)
        self.cmd_fifo = uvm_tlm_fifo("cmd_fifo", self)
        self.scoreboard = Scoreboard("scoreboard", self)
        self.cmd_monitor = Monitor("cmd_monitor", self, "get_cmd")
        self.result_monitor = Monitor("result_monitor", self, "get_result")
        self.cmd_mon_fifo = uvm_tlm_analysis_fifo("cmd_mon_fifo", self)
        self.result_mon_fifo = uvm_tlm_analysis_fifo("result_mon_fifo", self)

    def connect_phase(self):
        self.tester.bpp.connect(self.cmd_fifo.put_export)
        self.driver.bgp.connect(self.cmd_fifo.get_export)
        self.cmd_monitor.ap.connect(self.cmd_mon_fifo.analysis_export)
        self.result_monitor.ap.connect(self.result_mon_fifo.analysis_export)
        self.scoreboard.cmd_gp.connect(self.cmd_mon_fifo.nonblocking_get_export)
        self.scoreboard.result_gp.connect(self.result_mon_fifo.nonblocking_get_export)


class MaxAluEnv(RandomAluEnv):
    """Generate maximum operands"""

    def build_phase(self):
        uvm_factory().set_type_override_by_type(RandomTester, MaxTester)
        super().build_phase()


class RandomTest(uvm_test):
    """Run with random operands"""
    def build_phase(self):
        self.env = RandomAluEnv("env", self)


class MaxTest(uvm_test):
    """Run with max operands"""
    def build_phase(self):
        self.env = MaxAluEnv("env", self)


@cocotb.test()
async def random_test(dut):
    """Random operands"""
    ConfigDB().set(None, "*", "DUT", dut)
    await uvm_root().run_test("RandomTest")


@cocotb.test()
async def max_test(dut):
    """Maximum operands"""
    ConfigDB().set(None, "*", "DUT", dut)
    await uvm_root().run_test("MaxTest")
