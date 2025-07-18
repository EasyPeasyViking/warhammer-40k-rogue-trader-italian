import json
import os
import time
from anthropic import Anthropic
from typing import Dict, Any

class WH40KTranslator:
    def __init__(self, api_key: str = None, model: str = "claude-3-5-haiku-20241022", prompt_file: str = "prompt.txt"):
        """Initialize the translator with Anthropic API key"""
        if api_key is None:
            api_key = os.getenv('ANTHROPIC_API_KEY')

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")

        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.total_cost = 0.0
        self.cache_hits = 0
        self.cache_misses = 0

        # Model-specific configurations
        self.model_configs = {
            "claude-3-haiku-20240307": {
                "input_cost": 0.25,
                "cache_write_cost": 0.30,
                "cache_read_cost": 0.03,
                "output_cost": 1.25,
                "min_cache_tokens": 2048,
                "name": "Claude 3 Haiku"
            },
            "claude-3-5-haiku-20241022": {
                "input_cost": 0.80,
                "cache_write_cost": 1.00,
                "cache_read_cost": 0.08,
                "output_cost": 4.00,
                "min_cache_tokens": 2048,
                "name": "Claude 3.5 Haiku"
            },
            "claude-3-5-sonnet-20241022": {
                "input_cost": 3.00,
                "cache_write_cost": 3.75,
                "cache_read_cost": 0.30,
                "output_cost": 15.00,
                "min_cache_tokens": 1024,
                "name": "Claude 3.5 Sonnet"
            },
            "claude-sonnet-4-20250514": {
                "input_cost": 3.00,
                "cache_write_cost": 3.75,
                "cache_read_cost": 0.30,
                "output_cost": 15.00,
                "min_cache_tokens": 1024,
                "name": "Claude 4 Sonnet"
            }
        }

        self.config = self.model_configs.get(model, self.model_configs["claude-3-5-haiku-20241022"])

        # Load system prompt from external file
        # Extended system prompt (needs to be longer for Haiku models - 2048 tokens minimum)
        self.system_prompt = self.load_prompt(prompt_file)

    def load_prompt(self, prompt_file: str) -> str:
        """Load the system prompt from an external file"""
        if not os.path.exists(prompt_file):
            raise FileNotFoundError(f"Prompt file {prompt_file} not found.")
        with open(prompt_file, 'r', encoding='utf-8') as f:
            return f.read()

    def count_tokens_estimate(self, text: str) -> int:
        """Rough estimate of tokens (1 token ≈ 4 characters for English)"""
        return len(text) // 4

    def load_json_file(self, filepath: str) -> Dict[str, Any]:
        """Load the JSON file into memory"""
        print(f"Loading JSON file: {filepath}")
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"Successfully loaded {len(data.get('strings', {}))} strings")
            return data
        except FileNotFoundError:
            print(f"Error: File {filepath} not found")
            raise
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON format - {e}")
            raise

    def load_progress(self, progress_file: str) -> Dict[str, Dict[str, Any]]:
        """Load the already translated data"""
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    translated_data = json.load(f)
                print(f"Loaded progress: {len(translated_data)} IDs already translated")
                return translated_data
            except:
                print("Warning: Could not load progress file, starting fresh")
                return {}
        return {}

    def save_progress(self, progress_file: str, translated_data: Dict[str, Dict[str, Any]]):
        """Save the translated data to track progress"""
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(translated_data, f, ensure_ascii=False, indent=2)

    def load_or_create_output(self, output_file: str) -> Dict[str, Any]:
        """Load existing output file or create new structure"""
        if os.path.exists(output_file):
            try:
                with open(output_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                print("Warning: Could not load existing output file, creating new")

        return {"strings": {}}

    def translate_text(self, text: str) -> str:
        """Translate a single text using Anthropic API with prompt caching"""
        try:
            # Check estimated token count
            estimated_tokens = self.count_tokens_estimate(self.system_prompt)
            min_tokens = self.config["min_cache_tokens"]

            print(f"🔍 Estimated system prompt tokens: {estimated_tokens} (min required: {min_tokens})")

            if estimated_tokens < min_tokens:
                print(f"⚠️  Warning: System prompt may be too short for caching (need {min_tokens}+ tokens)")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"}
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": f"Translate this text to Italian:\n\n{text}"
                    }
                ]
            )

            # Enhanced cache usage logging with model-specific cost calculation
            if hasattr(response, 'usage') and response.usage:
                usage = response.usage
                print(f"📊 Token usage - Input: {usage.input_tokens}, Output: {usage.output_tokens}")

                # Determine cache status
                cache_created = hasattr(usage, 'cache_creation_input_tokens') and usage.cache_creation_input_tokens and usage.cache_creation_input_tokens > 0
                cache_hit = hasattr(usage, 'cache_read_input_tokens') and usage.cache_read_input_tokens and usage.cache_read_input_tokens > 0

                if cache_created:
                    print(f"🆕 Cache created: {usage.cache_creation_input_tokens} tokens")
                    self.cache_misses += 1
                elif cache_hit:
                    print(f"💾 Cache hit: {usage.cache_read_input_tokens} tokens (COST SAVED!)")
                    self.cache_hits += 1
                else:
                    print(f"❓ No cache activity detected")
                    self.cache_misses += 1

                # Calculate cost with model-specific pricing
                regular_input_tokens = usage.input_tokens
                cache_creation_cost = (usage.cache_creation_input_tokens or 0) * self.config["cache_write_cost"] / 1000000
                cache_read_cost = (usage.cache_read_input_tokens or 0) * self.config["cache_read_cost"] / 1000000
                regular_input_cost = regular_input_tokens * self.config["input_cost"] / 1000000
                output_cost = usage.output_tokens * self.config["output_cost"] / 1000000

                total_cost = cache_creation_cost + cache_read_cost + regular_input_cost + output_cost
                self.total_cost += total_cost

                print(f"💰 Cost breakdown:")
                print(f"   Cache creation: ${cache_creation_cost:.6f}")
                print(f"   Cache read: ${cache_read_cost:.6f}")
                print(f"   Regular input: ${regular_input_cost:.6f}")
                print(f"   Output: ${output_cost:.6f}")
                print(f"   Total: ${total_cost:.6f}")

            return response.content[0].text.strip()
        except Exception as e:
            print(f"Error translating text: {e}")
            raise

    def estimate_total_cost(self, total_strings: int, avg_input_tokens: int = 100, avg_output_tokens: int = 150):
        """Estimate total cost for the translation project"""
        system_tokens = self.count_tokens_estimate(self.system_prompt)

        # First translation (cache creation)
        first_cost = (system_tokens * self.config["cache_write_cost"] / 1000000 +
                     avg_input_tokens * self.config["input_cost"] / 1000000 +
                     avg_output_tokens * self.config["output_cost"] / 1000000)

        # Subsequent translations (cache hits)
        subsequent_cost = (system_tokens * self.config["cache_read_cost"] / 1000000 +
                          avg_input_tokens * self.config["input_cost"] / 1000000 +
                          avg_output_tokens * self.config["output_cost"] / 1000000)

        total_estimated = first_cost + (subsequent_cost * (total_strings - 1))

        # Compare with no-cache cost
        no_cache_cost = total_strings * ((system_tokens + avg_input_tokens) * self.config["input_cost"] / 1000000 +
                                        avg_output_tokens * self.config["output_cost"] / 1000000)

        savings = no_cache_cost - total_estimated
        savings_percent = (savings / no_cache_cost) * 100

        print(f"📊 Cost Estimation for {self.config['name']}:")
        print(f"   Estimated total cost: ${total_estimated:.4f}")
        print(f"   Without caching: ${no_cache_cost:.4f}")
        print(f"   Estimated savings: ${savings:.4f} ({savings_percent:.1f}%)")

        return total_estimated

    def translate_file(self,
                      input_file: str,
                      output_file: str,
                      progress_file: str = "translation_progress.json",
                      delay: float = 1.0):
        """Main translation function"""

        # Load input data
        input_data = self.load_json_file(input_file)
        strings_data = input_data.get('strings', {})

        # Load progress (translated data)
        translated_data = self.load_progress(progress_file)

        # Load or create output structure
        output_data = self.load_or_create_output(output_file)
        if 'strings' not in output_data:
            output_data['strings'] = {}

        # Merge progress data into output (in case output was corrupted)
        output_data['strings'].update(translated_data)

        total_strings = len(strings_data)
        completed = len(translated_data)

        print(f"🎮 Starting translation with {self.config['name']}")
        print(f"📊 Progress: {completed}/{total_strings} already completed")
        print(f"🔍 System prompt: {len(self.system_prompt)} chars (~{self.count_tokens_estimate(self.system_prompt)} tokens)")
        print(f"💰 Min cache tokens required: {self.config['min_cache_tokens']}")

        # Estimate total cost
        self.estimate_total_cost(total_strings - completed)
        print("=" * 60)

        try:
            for i, (string_id, string_data) in enumerate(strings_data.items(), 1):
                # Skip if already translated
                if string_id in translated_data:
                    continue

                text_to_translate = string_data.get('Text', '')

                # Skip empty texts
                if not text_to_translate.strip():
                    translation_entry = {
                        'Offset': string_data.get('Offset', 0),
                        'Text': text_to_translate
                    }
                    translated_data[string_id] = translation_entry
                    output_data['strings'][string_id] = translation_entry
                    continue

                print(f"Translating {i}/{total_strings} - ID: {string_id}")
                print(f"Original: {text_to_translate[:140]}{'...' if len(text_to_translate) > 140 else ''}")

                # Translate the text with caching
                translated_text = self.translate_text(text_to_translate)

                print(f"Translated: {translated_text[:140]}{'...' if len(translated_text) > 140 else ''}")

                # Show progress percentage
                progress_pct = (len(translated_data) + 1) / total_strings * 100
                print(f"Progress: {progress_pct:.1f}% ({len(translated_data) + 1}/{total_strings})")
                print(f"💾 Cache stats: {self.cache_hits} hits, {self.cache_misses} misses")

                # Calculate cache hit rate
                total_requests = self.cache_hits + self.cache_misses
                cache_hit_rate = (self.cache_hits / total_requests * 100) if total_requests > 0 else 0
                print(f"📊 Cache hit rate: {cache_hit_rate:.1f}%")

                print(f"💰 Total cost so far: ${self.total_cost:.4f}")
                print("-" * 50)

                # Create translation entry
                translation_entry = {
                    'Offset': string_data.get('Offset', 0),
                    'Text': translated_text
                }

                # Save to both progress and output immediately
                translated_data[string_id] = translation_entry
                output_data['strings'][string_id] = translation_entry

                # Save progress after each translation to prevent data loss
                self.save_progress(progress_file, translated_data)

                # Save output every 10 translations for performance
                if len(translated_data) % 10 == 0:
                    self.save_output(output_file, output_data)
                    print(f"Progress saved: {len(translated_data)}/{total_strings}")

                # Add delay to respect API rate limits and ensure cache persistence
                time.sleep(delay)

        except KeyboardInterrupt:
            print("\nTranslation interrupted by user")
        except Exception as e:
            print(f"Error during translation: {e}")
        finally:
            # Always save both progress and output
            self.save_progress(progress_file, translated_data)
            self.save_output(output_file, output_data)

            # Final statistics
            print(f"\n🎯 Final Statistics:")
            print(f"✅ Translations completed: {len(translated_data)}/{total_strings}")
            print(f"🤖 Model used: {self.config['name']}")
            print(f"💾 Cache hits: {self.cache_hits}")
            print(f"🆕 Cache misses: {self.cache_misses}")
            cache_hit_rate = (self.cache_hits / (self.cache_hits + self.cache_misses) * 100) if (self.cache_hits + self.cache_misses) > 0 else 0
            print(f"📊 Cache hit rate: {cache_hit_rate:.1f}%")
            print(f"💰 Total cost: ${self.total_cost:.4f}")

            # Cost savings estimation
            if self.cache_hits > 0:
                system_tokens = self.count_tokens_estimate(self.system_prompt)
                savings_per_hit = system_tokens * (self.config["input_cost"] - self.config["cache_read_cost"]) / 1000000
                total_savings = self.cache_hits * savings_per_hit
                print(f"💸 Estimated savings from caching: ${total_savings:.4f}")

            print(f"Final progress: {len(translated_data)}/{total_strings} completed")

    def save_output(self, output_file: str, output_data: Dict[str, Any]):
        """Save the output JSON file"""
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

def main():
    """Main function to run the translator"""
    import argparse

    parser = argparse.ArgumentParser(description='Translate Warhammer 40K Rogue Trader JSON file to Italian')
    parser.add_argument('input_file', help='Input JSON file path')
    parser.add_argument('output_file', help='Output JSON file path')
    parser.add_argument('--progress', default='translation_progress.json', help='Progress tracking file')
    parser.add_argument('--delay', type=float, default=1.0, help='Delay between API calls in seconds')
    parser.add_argument('--model', default='claude-3-5-haiku-20241022',
                       choices=['claude-3-haiku-20240307', 'claude-3-5-haiku-20241022',
                              'claude-3-5-sonnet-20241022', 'claude-sonnet-4-20250514'],
                       help='Claude model to use')
    parser.add_argument('--prompt', default='prompt.txt', help='System prompt file path')

    args = parser.parse_args()

    print("🎮 Warhammer 40K: Rogue Trader - Italian Translation Tool")
    print("🚀 Multi-model support with cost optimization")
    print("💰 Caching enabled for maximum savings")
    print("=" * 60)

    try:
        translator = WH40KTranslator(model=args.model)
        translator.translate_file(
            input_file=args.input_file,
            output_file=args.output_file,
            progress_file=args.progress,
            delay=args.delay
        )
    except Exception as e:
        print(f"Translation failed: {e}")
        return 1

    print("=" * 60)
    print("✅ Translation completed successfully!")
    return 0

if __name__ == "__main__":
    exit(main())