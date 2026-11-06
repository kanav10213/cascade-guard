# Sample receipts

These are made-up receipts to try Cascade Guard on. The shops and numbers are invented, not real bills.

```bash
cascade-guard receipts run --samples --approval-limit 5000 --currency INR
cascade-guard receipts run --samples --ai ollama:qwen2.5:7b --approval-limit 5000 --currency INR
```

- `restaurant-pune.txt`: CGST and SGST, then a round-off. The total is the net payable, 430.00, not 430.50.
- `laptop-bengaluru.txt`: lakh-style amounts and IGST. It is above the approval limit, so it needs a manager.
- `fuel-pune.txt`: a fuel slip paid by Paytm.
- `kirana-kothrud.txt`: cash and change. An AI often reads 500.00, the cash given, as the total.
- `kopi-house-kl.txt`: a Malaysian receipt with GST and 5-sen rounding. It prints no currency, but it is from Kuala
  Lumpur, so it is in RM even when the company default is INR.
- `cafe-smudged.txt`: an OCR-damaged date ("3I/O1/2O18"). It is stopped for a person instead of being guessed.
