package main

import (
	"database/sql"
)

// Identity is one person as WhatsApp knows them: the LID and the phone number that address
// the same account, and the names we have for them.
type Identity struct {
	LID      string
	Phone    string
	FullName string // from the user's address book
	PushName string // the name the person chose for themselves
}

// DisplayName is the address-book name when there is one, otherwise the push name.
func (i Identity) DisplayName() string {
	if i.FullName != "" {
		return i.FullName
	}
	return i.PushName
}

const identitySchema = `
	CREATE TABLE IF NOT EXISTS contacts_identity (
		id INTEGER PRIMARY KEY AUTOINCREMENT,
		lid TEXT,
		phone TEXT,
		full_name TEXT,
		push_name TEXT,
		updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
	);
	CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_identity_lid ON contacts_identity(lid) WHERE lid IS NOT NULL;
	CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_identity_phone ON contacts_identity(phone) WHERE phone IS NOT NULL;
`

type identityRow struct {
	id                     int64
	lid, phone, full, push string
}

func findIdentity(tx *sql.Tx, column, value string) (*identityRow, error) {
	if value == "" {
		return nil, nil
	}
	var r identityRow
	var lid, phone, full, push sql.NullString
	err := tx.QueryRow(
		"SELECT id, lid, phone, full_name, push_name FROM contacts_identity WHERE "+column+" = ?", value,
	).Scan(&r.id, &lid, &phone, &full, &push)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	r.lid, r.phone, r.full, r.push = lid.String, phone.String, full.String, push.String
	return &r, nil
}

// firstNonEmpty returns the first non-empty argument.
func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}

func nullable(s string) interface{} {
	if s == "" {
		return nil
	}
	return s
}

// UpsertIdentity records what is known about a person. A record is matched to an existing row by
// its LID or its phone; when it links a LID row and a phone row that were separate, they are
// merged. A value that is empty never erases one that is already stored.
func (store *MessageStore) UpsertIdentity(lid, phone, fullName, pushName string) error {
	if lid == "" && phone == "" {
		return nil
	}
	tx, err := store.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	byLID, err := findIdentity(tx, "lid", lid)
	if err != nil {
		return err
	}
	byPhone, err := findIdentity(tx, "phone", phone)
	if err != nil {
		return err
	}

	target := byLID
	if target == nil {
		target = byPhone
	}
	merged := identityRow{lid: lid, phone: phone, full: fullName, push: pushName}
	if target == nil {
		_, err = tx.Exec(
			"INSERT INTO contacts_identity (lid, phone, full_name, push_name) VALUES (?, ?, ?, ?)",
			nullable(lid), nullable(phone), nullable(fullName), nullable(pushName),
		)
		if err != nil {
			return err
		}
		return tx.Commit()
	}

	other := byPhone
	if byLID == nil || byPhone == nil || byLID.id == byPhone.id {
		other = nil
	}
	merged.lid = firstNonEmpty(lid, target.lid)
	merged.phone = firstNonEmpty(phone, target.phone)
	merged.full = firstNonEmpty(fullName, target.full)
	merged.push = firstNonEmpty(pushName, target.push)
	if other != nil {
		merged.lid = firstNonEmpty(merged.lid, other.lid)
		merged.phone = firstNonEmpty(merged.phone, other.phone)
		merged.full = firstNonEmpty(merged.full, other.full)
		merged.push = firstNonEmpty(merged.push, other.push)
		// free the unique indexes before the surviving row takes the values
		if _, err := tx.Exec("DELETE FROM contacts_identity WHERE id = ?", other.id); err != nil {
			return err
		}
	}
	_, err = tx.Exec(
		"UPDATE contacts_identity SET lid = ?, phone = ?, full_name = ?, push_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
		nullable(merged.lid), nullable(merged.phone), nullable(merged.full), nullable(merged.push), target.id,
	)
	if err != nil {
		return err
	}
	return tx.Commit()
}

// GetIdentity finds a person by LID or phone.
func (store *MessageStore) GetIdentity(id string) (Identity, bool, error) {
	if id == "" {
		return Identity{}, false, nil
	}
	var lid, phone, full, push sql.NullString
	err := store.db.QueryRow(
		"SELECT lid, phone, full_name, push_name FROM contacts_identity WHERE lid = ? OR phone = ? LIMIT 1", id, id,
	).Scan(&lid, &phone, &full, &push)
	if err == sql.ErrNoRows {
		return Identity{}, false, nil
	}
	if err != nil {
		return Identity{}, false, err
	}
	return Identity{LID: lid.String, Phone: phone.String, FullName: full.String, PushName: push.String}, true, nil
}
