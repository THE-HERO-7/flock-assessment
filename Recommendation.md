# Recommendation: washing-machine detection from 1-minute smart-meter data

*For a utility audience. Technical detail is in `RESULTS.md` and `DATA.md`.*

## What the model does

It estimates when a household's washing machine is running, and how much power it draws, using only the whole-home meter at 1-minute resolution. It was built on the REFIT data (UK homes) and tested on two homes it had never seen.

## How well it works

- It beats simple baselines (always off, average, typical weekly pattern): its error is about 12% to 19% lower on the two unseen homes.
- It finds most washes (it flags 41% to 83% of the minutes when the washer is really on, depending on the home and settings).
- **But it is imprecise.** About four in five minutes it flags are not washes, usually another heating appliance such as a kettle, dishwasher or heater. The energy of an individual wash is typically wrong by more than half.

## Suitable uses

1. **Rough timing of washing per home:** identifying homes where much of the washing happens in the evening peak, as the basis for a load-shifting message (see Section 4 below).
2. **Rough weekly wash counts and hour-of-day patterns,** especially summed across many homes, where random errors partly average out.
3. **Screening,** not decisions: a flag that triggers a cheap, reversible action such as a tip or an opt-in message.

## Not suitable

1. **Per-wash energy or cost** shown to a customer, or used for billing or disputes: individual cycle energy is too inaccurate.
2. **Appliance fault or replacement detection,** which needs reliable per-cycle shape and energy.
3. **Use in India or other markets without retraining.** The model learned UK machines and habits.
4. **15- or 30-minute meter data:** the signal the model relies on disappears (see Section 4.3).
5. **Acting on one home's result without checking:** the output threshold was tuned on a single validation home, and it helped some homes and hurt another.

## Before any rollout

Validate against real sub-meter readings in a sample of the target homes; choose the output threshold on several homes; and score at the level of whole washes (does a predicted wash overlap a real one?), which is closer to how the output would be used.

---

# Answers to Section 4

## 4.1 One practical opportunity, and how to measure it

**Opportunity: shifting washes out of the evening peak.** Washing is a flexible load, and in some homes much of it falls in the early evening: House 9 shows the strongest early-evening peak, with roughly 11% of its cycles starting at 17:00. A utility with a time-of-day tariff could identify homes with a high share of washes in the peak window and send them a message such as "washes between 16:00 and 20:00 cost more; running them after 20:00 would save you about X per month". This only needs to know when washes happen, not the exact energy of each one, so it tolerates the model's modest accuracy better than per-wash billing would.

**TODO:** add the measured peak share (16:00 to 20:00, a window I chose) per home, for cycles and for energy.

**How to measure whether it worked.**

- Randomly split eligible homes into a message group and a control group, stratified by baseline evening share, after a baseline period of about 8 weeks.
- Main outcome: the share of washing-machine cycles (and kWh) that start in the peak window, compared between the two groups before and after the messages (difference-in-differences).
- Secondary outcomes: total washer energy (to check that people did not simply wash more), opt-outs and complaints.
- Both groups use the same estimator, so its errors largely cancel in the comparison. Before relying on it, check the estimator against real sub-meter readings in a sample of homes. A power calculation is needed to size the groups.

## 4.2 Limitations

- **Overlapping appliance signatures.** The washer's heating burst looks like other heating loads, which limits precision. Simultaneous appliances also blur the aggregate.
- **Missing data.** Each home is missing 9% to 25% of minutes, with outages up to 62 days. The cleaned files are forward-filled, so a stuck sensor can look like data, and 1.5% to 7.6% of minutes carry the Issues flag. The raw Part 1 data hides missing values as zeros.
- **Household differences.** Machines differ in peak power (about 2.05 to 2.35 kW), cycle length (34 to 101 minutes) and energy per cycle (285 to 786 Wh), and habits differ (morning, night, weekend). The model was tested on only two unseen homes.
- **UK to India.** The model learned UK machines and habits. As far as I know, many Indian washing machines are semi-automatic or top-loading and may not heat water, so the 2 kW heating burst the model depends on could be absent; a water heater (geyser) of similar power could become a new lookalike. Appliance mix (air conditioning, inverters, pumps), power cuts, shared meters and usage habits also differ. The approach would need retraining and re-validation on Indian data before any use there.

## 4.3 What gets harder with 15-minute or 30-minute data

A wash lasts 30 to 100 minutes, so at 15 or 30 minutes it becomes only one to seven readings. The 5 to 15 minute heating burst is averaged with the low-power tail and with everything else running in that interval, and the sharp step edges that the model relies on disappear. Cycle start and end times, duration, peak power and energy per cycle can no longer be measured, and separating overlapping appliances becomes much harder. What remains possible is an estimate of usage by hour of day, weekly wash counts or a daily energy share, as probabilities rather than detected cycles.
