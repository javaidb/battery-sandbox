"""Example estimator: shows the test-bench interface, nothing more.

Coulomb counting is the baseline that module B2 starts from. Copy this file
to begin your own estimators (EKF, UKF, ...) and delete it whenever you like.
"""

from ._registry import estimator


@estimator("Coulomb counting (example)")
class CoulombCounting:
    def reset(self, ctx):
        self.soc = ctx["soc0_guess"]
        self.capacity_as = ctx["nominal_capacity_ah"] * 3600
        self.t_prev = None

    def step(self, t, current_a, voltage_v, temperature_c):
        if self.t_prev is not None:
            self.soc += current_a * (t - self.t_prev) / self.capacity_as
        self.t_prev = t
        return {"soc": self.soc}
