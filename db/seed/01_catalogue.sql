-- Demo catalogue: 2 specialties and 4 doctors (2 per specialty) with fixed ids. The app runs this
-- file only on the first start (when there are no doctors yet), in the same transaction as the
-- slot seed, which gives every doctor 5 days of slots.
-- Idempotent anyway, so running it by hand against a filled database changes nothing.
--
-- Names: no two doctors share a first or a last name, so "Dr. Layla" or "Dr. Mansoor" is never
-- ambiguous when a patient types only part of a name.

INSERT INTO specialties (id, name) VALUES
    (1, 'Dermatology'),
    (2, 'General Practice')
ON CONFLICT DO NOTHING;

INSERT INTO doctors (id, name, specialty_id) VALUES
    (1, 'Dr. Salma Nasser', 1),
    (2, 'Dr. Khalid Rahman', 2),
    (3, 'Dr. Layla Hassan', 1),
    (4, 'Dr. Yusuf Mansoor', 2)
ON CONFLICT DO NOTHING;
