---
slug: composite-integration
validation: lightweight
status: active
---

## Claim

Composite Simpson integration with 20 intervals estimates the integrals of
x cubed, sin(x), and exp(x) on [0, 1] with absolute error at most 0.0001.

## Measurement

Compare candidate error with trapezoid error at the same evaluation budget.
Analytic integrals provide ground truth. Seed modulo three selects the function.

## Stop condition

Test all three functions. An error above 0.0001 fails the claim. Stop the bounded
run after three cases; new functions need a new claim and another test.
