"""Five required attacks, plus Merkle inclusion and an isolated 51% example."""
import argparse
from dataclasses import replace

from blockchain.chain import Blockchain, POLICY, validate_chain
from blockchain.fork import chain_work, choose_chain
from blockchain.light_client import LightClient, make_proof
from blockchain.mining import create_mining_candidate, mine_block, has_valid_pow
from blockchain.transaction import create_signed_transaction as sign
from blockchain.wallet import Wallet


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def accept(result):
    require(result.accepted, str(result))


def fund(wallet):
    chain = Blockchain()
    chain.mine_pending(wallet.address, timestamp=chain.tip.header.timestamp + 1)
    return chain


def mine(chain, address):
    return chain.mine_pending(address, timestamp=chain.tip.header.timestamp + 1).block


def attack_scenarios(pause=False):
    alice, bob, carol = (Wallet.generate() for _ in range(3))
    chain = fund(alice)
    original = sign(alice, bob.address, 5, 1, 0)
    accept(chain.submit_transaction(original))
    old_block = mine(chain, carol.address)
    successor = mine(chain, carol.address)
    blocks = chain.blocks
    require(chain.validate().accepted, "Original history invalid")
    results = {}

    def show(number, title, outcome, expected):
        print(f"Result: accepted={outcome.accepted}, code={outcome.code}")
        print(outcome.reason)
        require(not outcome.accepted and outcome.code == expected, f"Attack {number} not rejected")
        results[number] = outcome.code
        print(f"ATTACK {number}: BLOCKED", flush=True)
        if pause:
            input("Enter to continue... ")

    print("\n1. ALTER AMOUNT IN AN OLD BLOCK (height 2)")
    tampered = replace(original, amount=9)
    broken = replace(old_block, transactions=(old_block.transactions[0], tampered))
    print(f"Old amount={original.amount}; changed amount={tampered.amount}")
    print("Signature valid:", tampered.verify_signature())
    print("Stored Merkle root matches:", broken.has_valid_merkle_root())
    show(1, "amount change", validate_chain((*blocks[:2], broken, successor)), "invalid_signature")

    print("\n2. REBUILD THE OLD BLOCK, INCLUDING ITS HASH AND POW")
    # Even give the attacker the owner's valid signature and a fresh PoW.
    # The unchanged successor still points to the original hash.
    legitimate_replacement = sign(alice, bob.address, 9, 1, 0)
    candidate = create_mining_candidate(blocks[:2], (legitimate_replacement,),
                                       carol.address, old_block.header.timestamp, POLICY)
    rebuilt = mine_block(candidate).block
    prefix_valid = validate_chain((*blocks[:2], rebuilt)).accepted
    print(f"Rebuilt prefix valid={prefix_valid}, Merkle valid={rebuilt.has_valid_merkle_root()}, "
          f"PoW valid={has_valid_pow(rebuilt)}")
    print("Original block hash: ", old_block.hash)
    print("Rebuilt block hash:  ", rebuilt.hash)
    print("Successor prev_hash: ", successor.header.previous_hash)
    require(prefix_valid and rebuilt.hash != old_block.hash, "Rebuild must be a valid new prefix")
    show(2, "broken link", validate_chain((*blocks[:2], rebuilt, successor)), "invalid_link")

    print("\n3. FORGE A SIGNATURE")
    victim = fund(alice)
    tx = sign(alice, bob.address, 7, 0, 0)
    forged = replace(tx, signature=f"{int(tx.signature[:2], 16) ^ 1:02x}" + tx.signature[2:])
    print("Original signature valid:", tx.verify_signature())
    print("Forged signature valid:", forged.verify_signature())
    show(3, "forgery", victim.submit_transaction(forged), "invalid_signature")
    require(not victim.pending(), "Forged transaction entered mempool")

    print("\n4. DOUBLE SPEND FULL BALANCE (different TXIDs and contiguous nonces)")
    available = fund(alice)
    first = sign(alice, bob.address, 50, 0, 0)
    second = sign(alice, carol.address, 50, 0, 1)
    accept(available.submit_transaction(first))
    print("First amount=50, nonce=0, accepted=True")
    print("Second amount=50, nonce=1, valid signature:", second.verify_signature())
    print("Account:", available.account_summary(alice.address))
    show(4, "double spend", available.submit_transaction(second), "insufficient_funds")
    require(available.pending() == (first,), "Second spend entered mempool")

    print("\n5. REPLAY A CONFIRMED TRANSACTION")
    exact = chain.submit_transaction(original)
    print(f"Exact replay: accepted={exact.accepted}, code={exact.code}")
    require(not exact.accepted and exact.code == "duplicate", "Exact replay accepted")
    different = sign(alice, carol.address, 5, 1, 0)
    print("Different TXID with previously consumed nonce 0:")
    print("Confirmed next_nonce:", chain.ledger.next_nonce(alice.address))
    show(5, "nonce replay", chain.submit_transaction(different), "invalid_nonce")
    require(chain.blocks == blocks and not chain.pending() and chain.validate().accepted,
            "Rejected attacks changed the honest history")
    print("\nPASS: all five required attacks rejected.")
    return results


def merkle_demo():
    alice, bob, carol = (Wallet.generate() for _ in range(3))
    chain = fund(alice)
    txs = [sign(alice, bob.address, 10, 1, 0), sign(alice, carol.address, 12, 1, 1)]
    for tx in txs:
        accept(chain.submit_transaction(tx))
    block = mine(chain, carol.address)
    ids = [tx.txid for tx in block.transactions]  # 3 leaves including coinbase
    proof = make_proof(ids, 2)
    light = LightClient(tuple(b.header for b in chain.blocks))
    valid = light.includes(block.header.height, ids[2], proof)
    bad_txid = light.includes(block.header.height, "0" * 64, proof)
    bad_proof = replace(proof, siblings=("0" * 64, *proof.siblings[1:]))
    invalid = light.includes(block.header.height, ids[2], bad_proof)
    print("\nEXTRA 1. HEADER-ONLY MERKLE PROOF")
    print(f"Stored headers={len(light.headers)}; block leaves={len(ids)}; proof hashes={len(proof.siblings)}")
    print("TXID:", ids[2])
    print("Merkle root:", block.header.merkle_root)
    print("Original proof:", valid, "| altered TXID:", bad_txid, "| altered sibling:", invalid)
    require(valid and not bad_txid and not invalid, "Incorrect proof verification")
    return valid


def majority_demo():
    """Controlled private fork with more work, demonstrating the LIMIT of PoW."""
    alice, merchant, receiver, honest_miner, attacker = (Wallet.generate() for _ in range(5))
    shared = fund(alice)
    public, private = Blockchain(shared.blocks), Blockchain(shared.blocks)
    pay = sign(alice, merchant.address, 50, 0, 0)
    conflict = sign(alice, receiver.address, 50, 0, 0)
    accept(public.submit_transaction(pay))
    accept(private.submit_transaction(conflict))
    mine(public, honest_miner.address)
    mine(private, attacker.address)
    tied = choose_chain(public, private.blocks)
    require(not tied.outcome.accepted and tied.outcome.code == "not_more_work", "Tie must keep public")
    mine(private, attacker.address)
    result = choose_chain(public, private.blocks)
    print("\nEXTRA 2. CONTROLLED PRIVATE FORK (MAJORITY ATTACK LIMIT)")
    print("Public work:", chain_work(public.blocks), "| private work:", chain_work(private.blocks))
    print("Merchant before / after:", public.balance(merchant.address), "/", result.chain.balance(merchant.address))
    print("Alternative recipient after:", result.chain.balance(receiver.address))
    print("Fork choice:", result.outcome.code, "| dropped orphan:", [code for _, code in result.dropped])
    require(result.outcome.accepted and result.common_height == 1
            and public.balance(merchant.address) == 50
            and result.chain.balance(merchant.address) == 0
            and result.chain.balance(receiver.address) == 50
            and (pay.txid, "invalid_nonce") in result.dropped,
            "Expected a valid heavier private fork and conflicting orphan rejection")
    print("EXPECTED LIMIT: valid greater-work chain is accepted; 51% is not prevented by PoW.")
    return result.outcome.code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pause", action="store_true")
    a = p.parse_args()
    attack_scenarios(a.pause)
    merkle_demo()
    if a.pause:
        input("Enter to continue... ")
    majority_demo()
    print("\nPASS: required attacks and both additional demonstrations complete.")


if __name__ == "__main__":
    main()
