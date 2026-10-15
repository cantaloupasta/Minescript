# [Experimentation Table Script](Minescript/Python/exp/exp.py)
### Fully autonomous
## How to use
- REQUIRES [LIB_INV](https://github.com/SmartBoty/Minescript/tree/main/lib_inv)
- Equip a Guardian Pet and enable pet visibility
- Have a couple million coins in your purse if you are missing XP Levels
- Start the script by looking at the experimentation table (must be in reach)
- On line 10, there is the variable "spend_bits" which is "True" by default, set to "False" if you want it off
- On line 11, there is the variable "is_serum" (means that you have consumed 3 metaphysical serums) which is "True" by default, set to "False" if you want it off.
<img width="932" height="92" alt="image" src="https://github.com/user-attachments/assets/1de7bf8c-4574-492a-9c00-fb88476e8055" />


## Features
- Plays the add-ons and super pair
- Replenishes charges with bits when on cooldown
- Buys Titanic Exp Bottles when low on XP levels
- Stops the script when a Guardian pet is not equipped or there are no more charges left
- Prioritizes items in super pairs
  1. Items > $1M
  2. Enchanting Exp
  3. Items < $1M
 
###### Note: unsure if the script ignores the guardian pet item in superpairs
  
