import threading


class Node:
    __slots__ = ("key", "left", "right", "deleted")

    def __init__(self, key):
        self.key = key
        self.left = None
        self.right = None
        self.deleted = False


class GlobalLock:
    policy = "один мʼютекс threading.Lock для всіх операцій"

    def __init__(self):
        self._lock = threading.Lock()

    def acquire_read(self):
        self._lock.acquire()

    def release_read(self):
        self._lock.release()

    def acquire_write(self):
        self._lock.acquire()

    def release_write(self):
        self._lock.release()


class RWLock:
    policy = "перевага читачам: новий читач входить, поки замок тримає хоч один читач; писець чекає, поки читачів не стане нуль"

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._writer = False

    def acquire_read(self):
        with self._cond:
            while self._writer:
                self._cond.wait()
            self._readers += 1

    def release_read(self):
        with self._cond:
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self):
        with self._cond:
            while self._writer or self._readers:
                self._cond.wait()
            self._writer = True

    def release_write(self):
        with self._cond:
            self._writer = False
            self._cond.notify_all()


class BST:
    def __init__(self):
        self.root = None

    def insert(self, key):
        if self.root is None:
            self.root = Node(key)
            return True
        node = self.root
        while True:
            if key == node.key:
                if node.deleted:
                    node.deleted = False
                    return True
                return False
            if key < node.key:
                if node.left is None:
                    node.left = Node(key)
                    return True
                node = node.left
            else:
                if node.right is None:
                    node.right = Node(key)
                    return True
                node = node.right

    def remove(self, key):
        node = self.root
        while node is not None:
            if key == node.key:
                if node.deleted:
                    return False
                node.deleted = True
                return True
            node = node.left if key < node.key else node.right
        return False

    def contains(self, key):
        node = self.root
        while node is not None:
            if key == node.key:
                return not node.deleted
            node = node.left if key < node.key else node.right
        return False

    def range_count(self, lo, hi):
        count = 0
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if lo < node.key:
                stack.append(node.left)
            if node.key < hi:
                stack.append(node.right)
            if lo <= node.key <= hi and not node.deleted:
                count += 1
        return count

    def check_invariants(self):
        keys = []
        stack, node = [], self.root
        while stack or node is not None:
            while node is not None:
                stack.append(node)
                node = node.left
            node = stack.pop()
            keys.append(node.key)
            node = node.right
        ordered = all(a < b for a, b in zip(keys, keys[1:]))
        return {"nodes": len(keys), "in_order_strictly_increasing": ordered, "unique_keys": len(set(keys)) == len(keys)}

    def snapshot(self):
        out = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node is not None:
                out.append((node.key, node.deleted, id(node.left), id(node.right)))
                stack.append(node.left)
                stack.append(node.right)
        return hash(tuple(out))
