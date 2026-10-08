"""Every block must carry an id before it is chunked or written.

Regression guard: block ids were once assigned only inside `parse_pdf`, so a producer that
builds blocks directly (the OHR-Bench JSON branch) reached `build_chunks` without one and
crashed with `KeyError: 'block_id'`.
"""
import unittest

from ingestion import ingest


def ohr_block(text='gt 文本'):
    """A block shaped exactly like the OHR-Bench branch builds it (no block_id, no bbox).

    OHR-Bench records carry only `page_idx` and `gt_text`; there is no geometry to record,
    so the honest shape is `bbox=None` (F01). The branch used to write `bbox=[0, 0, 0, 0]`,
    which the parse contract rejects as `bbox_inverted` — a fabricated locator, not a
    missing one. `ingest.ohr_block` is the single producer, and the test below keeps this
    fixture from drifting away from it.
    """
    return {'page_no': 1, 'block_type': 'paragraph', 'bbox': None, 'text': text,
            'heading_level': None, 'table_cells': None, 'reading_order': 0,
            'ocr_confidence': 1.0}


class BlockIdentityTests(unittest.TestCase):
    def test_the_fixture_matches_the_real_branch_builder(self):
        self.assertEqual(ingest.ohr_block({'page_idx': 0, 'gt_text': 'gt 文本'}), ohr_block())

    def test_a_block_without_an_id_gets_one(self):
        blocks = [ohr_block()]
        ingest.ensure_block_ids(blocks, 'doc-ohr')
        self.assertTrue(blocks[0]['block_id'].startswith('blk-'))

    def test_existing_ids_are_never_rewritten(self):
        # The contract mapping already named these; changing them would break the locator
        # identity that was validated against them.
        blocks = [{'block_id': 'blk-contract', 'reading_order': 0, 'text': 'x'}]
        ingest.ensure_block_ids(blocks, 'doc-x')
        self.assertEqual(blocks[0]['block_id'], 'blk-contract')

    def test_ids_are_stable_and_unique(self):
        first = [ohr_block('a'), ohr_block('b')]
        second = [ohr_block('a'), ohr_block('b')]
        ingest.ensure_block_ids(first, 'doc-1')
        ingest.ensure_block_ids(second, 'doc-1')
        self.assertEqual([b['block_id'] for b in first], [b['block_id'] for b in second])
        self.assertEqual(len({b['block_id'] for b in first}), 2)
        # Different documents must not collide either.
        other = [ohr_block('a')]
        ingest.ensure_block_ids(other, 'doc-2')
        self.assertNotEqual(other[0]['block_id'], first[0]['block_id'])

    def test_blocks_without_a_reading_order_still_get_distinct_ids(self):
        blocks = [{'text': 'a'}, {'text': 'b'}]
        ingest.ensure_block_ids(blocks, 'doc-1')
        self.assertEqual(len({b['block_id'] for b in blocks}), 2)

    def test_the_ohr_shaped_block_survives_chunking(self):
        blocks = [ohr_block()]
        ingest.ensure_block_ids(blocks, 'doc-ohr')
        chunks = ingest.build_chunks(blocks, 'doc-ohr')
        self.assertTrue(chunks)
        self.assertEqual(chunks[0]['block_ids'], [blocks[0]['block_id']])

    def test_chunking_an_unnamed_block_is_the_documented_failure(self):
        # Kept explicit so the guard above is not mistaken for incidental behaviour: the
        # fix is to name blocks first, not to make build_chunks silently tolerate this.
        with self.assertRaises(KeyError):
            ingest.build_chunks([ohr_block()], 'doc-unnamed')


if __name__ == '__main__':
    unittest.main()
