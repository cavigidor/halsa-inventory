"""M8-R5A / D-070: withdrawal of obsolete stock decision inputs, not a replacement stock model."""

RETIREMENT_CODE = "STOCK-THRESHOLDS-RETIRED"
RETIREMENT_REASON = ("Eski minseviye/maxseviye güncel stok hedefleri değildir; satın alma ve fazla stok "
                     "önerileri kaldırıldı. STOK Kontrol % karar girdisi olarak kullanılmaz. "
                     "İşaretli ERP bakiyesi fiziksel olarak kullanılabilir stok değildir.")
BALANCE_NOTE = ("İşaretli ERP bakiyesi fiziksel olarak kullanılabilir stok değildir. Negatif bakiye, satış veya "
                "parça sökümünü yansıtabilir; işareti değiştirilmez. Satış olmaması üretimden kalktığını göstermez.")
