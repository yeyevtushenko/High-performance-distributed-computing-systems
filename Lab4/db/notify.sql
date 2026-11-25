CREATE OR REPLACE FUNCTION tasks_notify() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('tasks_new', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tasks_notify ON tasks;
CREATE TRIGGER tasks_notify AFTER INSERT ON tasks FOR EACH ROW EXECUTE FUNCTION tasks_notify();
