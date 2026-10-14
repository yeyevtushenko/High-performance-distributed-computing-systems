CREATE TABLE IF NOT EXISTS donations (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    donor_code    VARCHAR(32) NOT NULL,
    blood_group   VARCHAR(2)  NOT NULL CHECK (blood_group IN ('O', 'A', 'B', 'AB')),
    rh_factor     CHAR(1)     NOT NULL CHECK (rh_factor IN ('+', '-')),
    volume_ml     SMALLINT    NOT NULL CHECK (volume_ml BETWEEN 200 AND 500),
    donation_date DATE        NOT NULL,
    is_suitable   BOOLEAN     NOT NULL
);

CREATE INDEX IF NOT EXISTS donations_blood_group_id_idx ON donations (blood_group, id);
