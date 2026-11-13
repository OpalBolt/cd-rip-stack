# MakeMKV key expiry: remind, never auto-fetch

When a scan reports MakeMKV's evaluation expired, skive names the cause, the exact file to edit (`~/.MakeMKV/settings.conf` on the rip box) and the free-key forum topic (forum.makemkv.com topic t=1053), then refuses the run — it never fetches the key or writes settings.conf itself. Scraping the forum is fragile (makemkv.com goes down exactly at key rotation), and writing a licence file from scraped web content is a trust boundary not worth crossing to save one manual paste a month.
